from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transcriber_app import create_app
from transcriber_app.__main__ import main as native_main
from transcriber_app.app import _stream_upload
from transcriber_app.config import Settings
from transcriber_app.media import MediaError, MediaInfo, MediaTools
from transcriber_app.security import sanitize_filename
from transcriber_app.whisper_adapter import WhisperAdapter


class FakeMediaTools:
    def probe(self, media_path: Path) -> MediaInfo:
        assert media_path.is_file()
        return MediaInfo(duration_seconds=1.0, format_name="mov")

    def decode_wav(self, media_path: Path, wav_path: Path, duration_seconds: float) -> None:
        assert duration_seconds == 1.0
        wav_path.write_bytes(b"RIFF-fake-wave")


class FakeTranscriber:
    def __init__(self, text: str = "The whole local transcript.") -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, wav_path: Path) -> str:
        assert wav_path.read_bytes() == b"RIFF-fake-wave"
        self.calls += 1
        return self.text


def test_native_entrypoint_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    run_options: dict[str, object] = {}

    class FakeApp:
        def run(self, **kwargs) -> None:
            run_options.update(kwargs)

    monkeypatch.delenv("TRANSCRIBER_BIND_HOST", raising=False)
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setattr("transcriber_app.__main__.create_app", lambda: FakeApp())

    native_main()

    assert run_options["host"] == "127.0.0.1"
    assert run_options["port"] == 8123
    assert run_options["threaded"] is True
    assert run_options["use_reloader"] is False


@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TRANSCRIPTS_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("WHISPER_CACHE_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "1024")
    monkeypatch.setenv("TRANSCRIBER_QUEUE_SIZE", "2")
    return tmp_path


def _app(fake: FakeTranscriber | None = None):
    return create_app(
        {"TESTING": True},
        transcriber=fake or FakeTranscriber(),
        media_tools=FakeMediaTools(),
    )


def _csrf(client) -> str:
    response = client.get("/api/config")
    assert response.status_code == 200
    return response.get_json()["csrfToken"]


def _wait_for_terminal(client, job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").get_json()["job"]
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not reach a terminal state")


def test_health_is_lightweight_and_identifies_service(configured_env: Path) -> None:
    fake = FakeTranscriber()
    app = _app(fake)
    try:
        response = app.test_client().get("/healthz")
        assert response.status_code == 200
        assert response.get_json() == {
            "status": "ok",
            "service": "local-whisper-transcriber",
            "queueDepth": 0,
            "queueCapacity": 2,
        }
        assert fake.calls == 0
    finally:
        app.extensions["job_manager"].shutdown()


def test_upload_transcribe_download_and_cleanup(configured_env: Path) -> None:
    fake = FakeTranscriber("First line.\nSecond line.")
    app = _app(fake)
    client = app.test_client()
    try:
        token = _csrf(client)
        response = client.post(
            "/api/jobs",
            data={"file": (io.BytesIO(b"local-media"), "CON.mov")},
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": token, "Origin": "http://localhost"},
        )
        assert response.status_code == 202
        queued = response.get_json()["job"]
        assert queued["filename"] == "_CON.mov"
        job = _wait_for_terminal(client, queued["id"])
        assert job["status"] == "completed"
        assert job["downloadUrl"] == f"/api/jobs/{queued['id']}/download"

        download = client.get(job["downloadUrl"])
        assert download.status_code == 200
        assert download.data.decode("utf-8") == "First line.\nSecond line.\n"
        assert "attachment" in download.headers["Content-Disposition"]
        assert fake.calls == 1
        assert list((configured_env / "work").iterdir()) == []
        assert [path.name for path in (configured_env / "transcripts").iterdir()] == [
            "_CON_transcript.txt"
        ]
    finally:
        app.extensions["job_manager"].shutdown()


def test_csrf_origin_host_and_uuid_guards(configured_env: Path) -> None:
    app = _app()
    client = app.test_client()
    try:
        token = _csrf(client)
        upload = lambda: {"file": (io.BytesIO(b"media"), "clip.mp4")}
        missing_token = client.post(
            "/api/jobs", data=upload(), content_type="multipart/form-data"
        )
        assert missing_token.status_code == 403

        bad_origin = client.post(
            "/api/jobs",
            data=upload(),
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
        )
        assert bad_origin.status_code == 403

        bad_host = client.get("/healthz", headers={"Host": "evil.example"})
        assert bad_host.status_code == 400
        assert client.get("/api/jobs/not-a-uuid").status_code == 404
    finally:
        app.extensions["job_manager"].shutdown()


def test_upload_limit_and_extension_allowlist(
    configured_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "4")
    app = _app()
    client = app.test_client()
    try:
        token = _csrf(client)
        too_large = client.post(
            "/api/jobs",
            data={"file": (io.BytesIO(b"12345"), "clip.mp4")},
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": token},
        )
        assert too_large.status_code == 413
        unsupported = client.post(
            "/api/jobs",
            data={"file": (io.BytesIO(b"123"), "notes.txt")},
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": token},
        )
        assert unsupported.status_code == 415
        assert list((configured_env / "work").iterdir()) == []
    finally:
        app.extensions["job_manager"].shutdown()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../family recording.mov", "family_recording.mov"),
        ("NUL.mp4", "_NUL.mp4"),
        ("LPT9.wav", "_LPT9.wav"),
        ("...", "recording"),
    ],
)
def test_filename_sanitization(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_ffprobe_uses_local_protocol_allowlist_and_rejects_playlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media_path = tmp_path / "opaque.mov"
    media_path.write_bytes(b"media")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [{"codec_type": "audio"}],
                    "format": {"duration": "2.5", "format_name": "hls"},
                }
            ),
        )

    monkeypatch.setattr("transcriber_app.media.subprocess.run", fake_run)
    tools = MediaTools(
        ffprobe_binary="ffprobe",
        ffmpeg_binary="ffmpeg",
        max_duration_seconds=60,
        timeout_seconds=5,
    )
    with pytest.raises(MediaError, match="not supported"):
        tools.probe(media_path)
    assert calls[0][calls[0].index("-protocol_whitelist") + 1] == "file,pipe"
    assert calls[0][-2:] == ["-i", str(media_path)]


def test_ffmpeg_decode_discards_unused_process_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media_path = tmp_path / "recording.mov"
    wav_path = tmp_path / "recording.wav"
    media_path.write_bytes(b"media")
    call_options: dict[str, object] = {}

    def fake_run(command, **kwargs):
        call_options.update(kwargs)
        wav_path.write_bytes(b"RIFF-fake-wave")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("transcriber_app.media.subprocess.run", fake_run)
    tools = MediaTools(
        ffprobe_binary="ffprobe",
        ffmpeg_binary="ffmpeg",
        max_duration_seconds=60,
        timeout_seconds=5,
    )
    tools.decode_wav(media_path, wav_path, 2.5)

    assert call_options["stdin"] is subprocess.DEVNULL
    assert call_options["stdout"] is subprocess.DEVNULL
    assert call_options["stderr"] is subprocess.DEVNULL
    assert call_options["shell"] is False


def test_max_upload_mb_compatibility(
    configured_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAX_UPLOAD_BYTES")
    monkeypatch.setenv("MAX_UPLOAD_MB", "7")
    app = _app()
    try:
        payload = app.test_client().get("/api/config").get_json()
        assert payload["maxUploadBytes"] == 7 * 1024 * 1024
        assert payload["model"] == "base.en"
        assert payload["device"] == "auto"
    finally:
        app.extensions["job_manager"].shutdown()


def test_default_upload_limit_is_four_gib(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)

    assert Settings.from_env().max_upload_bytes == 4 * 1024 * 1024 * 1024


def test_startup_removes_only_stale_app_owned_work(
    configured_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = configured_env / "work"
    work_dir.mkdir()
    stale_owned = work_dir / ("a" * 32 + ".mov")
    fresh_owned = work_dir / ("b" * 32 + ".mp4")
    unrelated = work_dir / "keep-me.mov"
    for path in (stale_owned, fresh_owned, unrelated):
        path.write_bytes(b"x")
    old = time.time() - 120
    os.utime(stale_owned, (old, old))
    monkeypatch.setenv("STALE_WORK_MAX_AGE_SECONDS", "60")

    app = _app()
    try:
        assert not stale_owned.exists()
        assert fresh_owned.exists()
        assert unrelated.exists()
    finally:
        app.extensions["job_manager"].shutdown()


def test_bare_cuda_selects_safest_gpu_with_cpu_fallback(
    configured_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = WhisperAdapter(
        model_name="base.en",
        requested_device="cuda",
        cache_dir=configured_env / "models",
    )

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def device_count() -> int:
            return 2

        @staticmethod
        def mem_get_info(index: int) -> tuple[int, int]:
            free_mb = (900, 2800)[index]
            return free_mb * 1024 * 1024, 8 * 1024 * 1024 * 1024

    torch = SimpleNamespace(cuda=FakeCuda())
    assert adapter._select_device(torch) == "cuda:1"
    monkeypatch.setenv("TRANSCRIBER_MIN_FREE_VRAM_MB", "3000")
    assert adapter._select_device(torch) == "cpu"


def test_interrupted_stream_removes_partial_upload(tmp_path: Path) -> None:
    class InterruptedStream:
        calls = 0

        def read(self, _: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise RuntimeError("client disconnected")

    destination = tmp_path / "partial.mp4"
    with pytest.raises(RuntimeError, match="disconnected"):
        _stream_upload(InterruptedStream(), destination, 100)
    assert not destination.exists()
