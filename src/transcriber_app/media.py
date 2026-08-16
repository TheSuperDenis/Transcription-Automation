"""Strict local-media inspection and decoding via FFmpeg."""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaError(Exception):
    """A media failure with a user-safe message."""

    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    format_name: str


class MediaTools:
    # Playlists and raw data demuxers are intentionally absent. Containers here
    # are self-contained formats expected from common recording applications.
    SAFE_FORMATS = frozenset(
        {
            "flac",
            "matroska",
            "mov",
            "mp3",
            "mp4",
            "m4a",
            "ogg",
            "wav",
            "webm",
        }
    )

    def __init__(
        self,
        *,
        ffprobe_binary: str,
        ffmpeg_binary: str,
        max_duration_seconds: int,
        timeout_seconds: int,
    ) -> None:
        self.ffprobe_binary = ffprobe_binary
        self.ffmpeg_binary = ffmpeg_binary
        self.max_duration_seconds = max_duration_seconds
        self.timeout_seconds = timeout_seconds

    def probe(self, media_path: Path) -> MediaInfo:
        command = [
            self.ffprobe_binary,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,duration",
            "-select_streams",
            "a",
            "-of",
            "json",
            "-i",
            str(media_path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                check=False,
                timeout=self.timeout_seconds,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise MediaError("FFprobe is not installed in the transcription service.") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaError("The recording took too long to inspect.") from exc

        if result.returncode != 0:
            raise MediaError("The selected file is not a readable recording.")
        try:
            payload = json.loads(result.stdout)
            streams = payload.get("streams") or []
            format_data = payload.get("format") or {}
            format_names = {
                item.strip().lower()
                for item in str(format_data.get("format_name", "")).split(",")
                if item.strip()
            }
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise MediaError("The recording metadata could not be read.") from exc

        if not streams or not any(stream.get("codec_type") == "audio" for stream in streams):
            raise MediaError("The selected file does not contain an audio stream.")
        if not format_names.intersection(self.SAFE_FORMATS):
            raise MediaError("This media container is not supported for safe local decoding.")

        raw_duration = format_data.get("duration")
        if raw_duration in (None, "N/A"):
            raw_duration = next(
                (stream.get("duration") for stream in streams if stream.get("duration")),
                None,
            )
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError) as exc:
            raise MediaError("The recording duration could not be determined.") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise MediaError("The recording duration is invalid.")
        if duration > self.max_duration_seconds:
            hours = self.max_duration_seconds / 3600
            raise MediaError(f"The recording exceeds the {hours:g}-hour duration limit.")
        return MediaInfo(duration_seconds=duration, format_name=sorted(format_names)[0])

    def decode_wav(self, media_path: Path, wav_path: Path, duration_seconds: float) -> None:
        # A self-contained PCM WAV prevents Whisper from reopening an untrusted
        # container and constrains protocols before the model sees any content.
        command = [
            self.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(media_path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            "-y",
            str(wav_path),
        ]
        # Long recordings need longer than the probe timeout, while malformed
        # inputs must still have a firm wall-clock bound.
        decode_timeout = max(self.timeout_seconds, int(duration_seconds * 2) + 30)
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=decode_timeout,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise MediaError("FFmpeg is not installed in the transcription service.") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaError("The recording took too long to decode.") from exc
        if result.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size == 0:
            raise MediaError("The recording audio could not be decoded.")
