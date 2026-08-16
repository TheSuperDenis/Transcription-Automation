"""A bounded, single-worker transcription queue."""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .media import MediaError, MediaTools
from .whisper_adapter import TranscriptionError


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Transcriber(Protocol):
    def transcribe(self, wav_path: Path) -> str: ...


class QueueFullError(Exception):
    pass


@dataclass
class Job:
    id: str
    filename: str
    upload_path: Path
    model: str | None = None
    device: str | None = None
    status: str = "queued"
    created_at: str = field(default_factory=_timestamp)
    updated_at: str = field(default_factory=_timestamp)
    output_path: Path | None = None
    error: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "filename": self.filename,
            "model": self.model,
            "device": self.device,
            "downloadUrl": (
                f"/api/jobs/{self.id}/download" if self.status == "completed" else None
            ),
            "error": self.error,
        }


class JobManager:
    def __init__(
        self,
        *,
        media_tools: MediaTools,
        transcriber: Transcriber,
        work_dir: Path,
        transcripts_dir: Path,
        queue_size: int,
        start_worker: bool = True,
    ) -> None:
        self.media_tools = media_tools
        self.transcriber = transcriber
        self.work_dir = work_dir.resolve()
        self.transcripts_dir = transcripts_dir.resolve()
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self._output_lock = threading.Lock()
        self._queue: queue.Queue[Job | None] = queue.Queue(maxsize=queue_size)
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(
                target=self._run,
                name="transcription-worker",
                daemon=True,
            )
            self._worker.start()

    @property
    def queue_capacity(self) -> int:
        return self._queue.maxsize

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def submit(self, *, filename: str, upload_path: Path) -> Job:
        job = Job(
            id=str(uuid.uuid4()),
            filename=filename,
            upload_path=upload_path,
            model=getattr(self.transcriber, "model_name", None),
            device=getattr(self.transcriber, "requested_device", None),
        )
        with self._jobs_lock:
            self._jobs[job.id] = job
        try:
            self._queue.put_nowait(job)
        except queue.Full as exc:
            with self._jobs_lock:
                self._jobs.pop(job.id, None)
            upload_path.unlink(missing_ok=True)
            raise QueueFullError from exc
        return job

    def get(self, job_id: str) -> Job | None:
        try:
            normalized = str(uuid.UUID(job_id))
        except (ValueError, AttributeError, TypeError):
            return None
        with self._jobs_lock:
            return self._jobs.get(normalized)

    def _set_state(self, job: Job, status: str, *, error: str | None = None) -> None:
        with self._jobs_lock:
            job.status = status
            job.updated_at = _timestamp()
            job.error = error

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                self._queue.task_done()
                return
            try:
                self._process(job)
            finally:
                self._queue.task_done()

    def _process(self, job: Job) -> None:
        wav_path = self.work_dir / f"{job.id}.wav"
        self._set_state(job, "running")
        try:
            media_info = self.media_tools.probe(job.upload_path)
            self.media_tools.decode_wav(
                job.upload_path, wav_path, media_info.duration_seconds
            )
            transcript = self.transcriber.transcribe(wav_path)
            output_path = self._write_transcript(job.filename, transcript)
            with self._jobs_lock:
                job.output_path = output_path
                job.model = getattr(self.transcriber, "model_name", job.model)
                job.device = getattr(self.transcriber, "active_device", job.device)
            self._set_state(job, "completed")
        except (MediaError, TranscriptionError) as exc:
            self._set_state(job, "failed", error=exc.public_message)
        except Exception:
            # Do not log model output, FFmpeg stderr, or transcript content.
            self._set_state(job, "failed", error="Transcription failed unexpectedly.")
        finally:
            job.upload_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)

    def _write_transcript(self, input_filename: str, transcript: str) -> Path:
        stem = Path(input_filename).stem or "recording"
        with self._output_lock:
            output_path = self.transcripts_dir / f"{stem}_transcript.txt"
            counter = 2
            while output_path.exists():
                output_path = self.transcripts_dir / f"{stem}_transcript_{counter}.txt"
                counter += 1

            fd, temporary_name = tempfile.mkstemp(
                prefix=".transcript-", suffix=".tmp", dir=self.transcripts_dir
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(transcript)
                    if transcript and not transcript.endswith("\n"):
                        handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, output_path)
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
        return output_path

    def shutdown(self, timeout: float = 5.0) -> None:
        if not self._worker or not self._worker.is_alive():
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            return
        self._worker.join(timeout=timeout)
