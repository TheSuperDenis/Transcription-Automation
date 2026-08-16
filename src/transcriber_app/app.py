"""Flask application factory for the local-only transcription UI."""

from __future__ import annotations

import hmac
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

from .config import ACCEPTED_EXTENSIONS, Settings
from .jobs import JobManager, QueueFullError, Transcriber
from .media import MediaTools
from .security import origin_matches_request, request_hostname, sanitize_filename
from .whisper_adapter import WhisperAdapter


def create_app(
    config_overrides: dict[str, Any] | None = None,
    *,
    transcriber: Transcriber | None = None,
    media_tools: MediaTools | None = None,
    start_worker: bool = True,
) -> Flask:
    settings = Settings.from_env()
    settings.ensure_directories()
    _cleanup_stale_work_files(settings)
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.update(
        MAX_CONTENT_LENGTH=settings.max_upload_bytes + 2 * 1024 * 1024,
        TRANSCRIBER_SETTINGS=settings,
        CSRF_TOKEN=secrets.token_urlsafe(32),
    )
    if config_overrides:
        app.config.update(config_overrides)

    if media_tools is None:
        media_tools = MediaTools(
            ffprobe_binary=settings.ffprobe_binary,
            ffmpeg_binary=settings.ffmpeg_binary,
            max_duration_seconds=settings.max_duration_seconds,
            timeout_seconds=settings.media_timeout_seconds,
        )
    if transcriber is None:
        transcriber = WhisperAdapter(
            model_name=settings.whisper_model,
            requested_device=settings.device,
            cache_dir=settings.whisper_cache_dir,
            language=settings.language,
        )
    manager = JobManager(
        media_tools=media_tools,
        transcriber=transcriber,
        work_dir=settings.work_dir,
        transcripts_dir=settings.transcripts_dir,
        queue_size=settings.queue_size,
        start_worker=start_worker,
    )
    app.extensions["job_manager"] = manager

    @app.before_request
    def protect_local_boundary() -> Response | None:
        hostname = request_hostname(request.host)
        if hostname not in settings.allowed_hosts:
            return _error("Invalid Host header.", 400)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if origin and not origin_matches_request(origin, request.host, request.scheme):
                return _error("Cross-origin requests are not allowed.", 403)
            if request.path.startswith("/api/"):
                supplied = request.headers.get("X-CSRF-Token", "")
                expected = str(app.config["CSRF_TOKEN"])
                if not hmac.compare_digest(supplied, expected):
                    return _error("A valid CSRF token is required.", 403)
        return None

    @app.after_request
    def set_security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_: RequestEntityTooLarge) -> tuple[Response, int]:
        return _error("The upload exceeds the configured size limit.", 413)

    @app.get("/")
    def index() -> Response:
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/healthz")
    def health() -> Response:
        # Intentionally does not import Whisper, load a model, or inspect a GPU.
        return jsonify(
            {
                "status": "ok",
                "service": "local-whisper-transcriber",
                "queueDepth": manager.queue_depth(),
                "queueCapacity": manager.queue_capacity,
            }
        )

    @app.get("/api/config")
    def client_config() -> Response:
        return jsonify(
            {
                "csrfToken": app.config["CSRF_TOKEN"],
                "acceptedExtensions": sorted(ACCEPTED_EXTENSIONS),
                "maxUploadBytes": settings.max_upload_bytes,
                "model": settings.whisper_model,
                "device": settings.device,
                "openOutputFolderSupported": False,
            }
        )

    @app.post("/api/jobs")
    def create_job() -> tuple[Response, int]:
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            return _error("Choose a recording to transcribe.", 400)
        safe_name = sanitize_filename(uploaded.filename)
        extension = Path(safe_name).suffix.lower()
        if extension not in ACCEPTED_EXTENSIONS:
            return _error("That recording type is not supported.", 415)

        upload_path = settings.work_dir / f"{uuid.uuid4().hex}{extension}"
        try:
            _stream_upload(uploaded.stream, upload_path, settings.max_upload_bytes)
            job = manager.submit(filename=safe_name, upload_path=upload_path)
        except UploadTooLarge:
            upload_path.unlink(missing_ok=True)
            return _error("The upload exceeds the configured size limit.", 413)
        except QueueFullError:
            return _error("The transcription queue is full. Try again shortly.", 503)
        except OSError:
            upload_path.unlink(missing_ok=True)
            return _error("The recording could not be stored for transcription.", 500)
        return jsonify({"job": job.public_dict()}), 202

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str) -> tuple[Response, int] | Response:
        job = manager.get(job_id)
        if job is None:
            return _error("Transcription job not found.", 404)
        return jsonify({"job": job.public_dict()})

    @app.get("/api/jobs/<job_id>/download")
    def download(job_id: str) -> tuple[Response, int] | Response:
        job = manager.get(job_id)
        if job is None:
            return _error("Transcription job not found.", 404)
        if job.status != "completed" or job.output_path is None:
            return _error("The transcript is not ready.", 409)
        try:
            output_path = job.output_path.resolve(strict=True)
            output_path.relative_to(settings.transcripts_dir.resolve())
        except (OSError, ValueError):
            return _error("The transcript is unavailable.", 404)
        return send_from_directory(
            settings.transcripts_dir,
            output_path.name,
            as_attachment=True,
            download_name=output_path.name,
            mimetype="text/plain; charset=utf-8",
        )

    return app


class UploadTooLarge(Exception):
    pass


def _stream_upload(stream: Any, destination: Path, max_bytes: int) -> None:
    total = 0
    try:
        with destination.open("xb") as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLarge
                output.write(chunk)
        if total == 0:
            raise OSError("empty upload")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _error(message: str, status: int) -> tuple[Response, int]:
    return jsonify({"error": {"message": message, "status": status}}), status


_OWNED_WORK_NAMES = re.compile(
    r"(?:[0-9a-f]{32}\.(?:flac|m4a|mkv|mov|mp3|mp4|ogg|wav|webm)"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.wav)"
)


def _cleanup_stale_work_files(settings: Settings) -> None:
    """Remove only old files with names this app owns; never recurse."""

    cutoff = time.time() - settings.stale_work_max_age_seconds
    try:
        entries = list(settings.work_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        if not _OWNED_WORK_NAMES.fullmatch(entry.name):
            continue
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            continue
