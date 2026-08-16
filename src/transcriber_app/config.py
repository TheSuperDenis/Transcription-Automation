"""Environment-backed configuration for the local transcription service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ACCEPTED_EXTENSIONS = frozenset(
    {".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".ogg", ".wav", ".webm"}
)


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _upload_limit_bytes() -> int:
    if os.getenv("MAX_UPLOAD_BYTES") is not None:
        return _positive_int("MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024)
    return _positive_int("MAX_UPLOAD_MB", 2048) * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    transcripts_dir: Path
    work_dir: Path
    whisper_cache_dir: Path
    whisper_model: str
    device: str
    max_upload_bytes: int
    max_duration_seconds: int
    queue_size: int
    media_timeout_seconds: int
    stale_work_max_age_seconds: int
    allowed_hosts: frozenset[str]
    ffmpeg_binary: str
    ffprobe_binary: str
    language: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        default_data = Path(os.getenv("TRANSCRIBER_DATA_DIR", "/data"))
        language = os.getenv("WHISPER_LANGUAGE", "").strip() or None
        allowed_hosts = frozenset(
            host.strip().lower().strip("[]")
            for host in os.getenv(
                "TRANSCRIBER_ALLOWED_HOSTS", "127.0.0.1,localhost,::1"
            ).split(",")
            if host.strip()
        )
        if not allowed_hosts:
            raise ValueError("TRANSCRIBER_ALLOWED_HOSTS cannot be empty")

        return cls(
            transcripts_dir=Path(
                os.getenv("TRANSCRIPTS_DIR", str(default_data / "transcripts"))
            ),
            work_dir=Path(os.getenv("WORK_DIR", str(default_data / "work"))),
            whisper_cache_dir=Path(
                os.getenv("WHISPER_CACHE_DIR", str(default_data / "models"))
            ),
            whisper_model=os.getenv("WHISPER_MODEL", "base.en").strip(),
            device=os.getenv("TRANSCRIBER_DEVICE", "auto").strip().lower(),
            max_upload_bytes=_upload_limit_bytes(),
            max_duration_seconds=_positive_int(
                "MAX_DURATION_SECONDS", 6 * 60 * 60
            ),
            queue_size=_positive_int("TRANSCRIBER_QUEUE_SIZE", 4),
            media_timeout_seconds=_positive_int("MEDIA_TIMEOUT_SECONDS", 120),
            stale_work_max_age_seconds=_positive_int(
                "STALE_WORK_MAX_AGE_SECONDS", 24 * 60 * 60
            ),
            allowed_hosts=allowed_hosts,
            ffmpeg_binary=os.getenv("FFMPEG_BINARY", "ffmpeg"),
            ffprobe_binary=os.getenv("FFPROBE_BINARY", "ffprobe"),
            language=language,
        )

    def ensure_directories(self) -> None:
        for directory in (
            self.transcripts_dir,
            self.work_dir,
            self.whisper_cache_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
