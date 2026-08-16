"""Lazy adapter around the official openai-whisper Python package."""

from __future__ import annotations

import gc
import os
import threading
from pathlib import Path
from typing import Any


class TranscriptionError(Exception):
    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


class WhisperAdapter:
    ALLOWED_MODELS = frozenset(
        {
            "tiny",
            "tiny.en",
            "base",
            "base.en",
            "small",
            "small.en",
            "medium",
            "medium.en",
            "large-v1",
            "large-v2",
            "large-v3",
            "large",
            "turbo",
        }
    )
    # Conservative free-memory floor, including headroom for decoding tensors.
    MODEL_VRAM_MB = {
        "tiny": 1200,
        "base": 1600,
        "small": 3000,
        "medium": 6500,
        "large": 11500,
        "turbo": 7500,
    }

    def __init__(
        self,
        *,
        model_name: str,
        requested_device: str,
        cache_dir: Path,
        language: str | None = None,
    ) -> None:
        if model_name not in self.ALLOWED_MODELS:
            raise ValueError(f"Unsupported WHISPER_MODEL: {model_name}")
        if not self._valid_device(requested_device):
            raise ValueError("TRANSCRIBER_DEVICE must be auto, cpu, cuda, or cuda:<index>")
        self.model_name = model_name
        self.requested_device = requested_device
        self.cache_dir = cache_dir
        self.language = language
        self._model: Any | None = None
        self._active_device: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _valid_device(device: str) -> bool:
        if device in {"auto", "cpu", "cuda"}:
            return True
        if not device.startswith("cuda:"):
            return False
        try:
            return int(device.split(":", 1)[1]) >= 0
        except ValueError:
            return False

    def _model_family(self) -> str:
        name = self.model_name.removesuffix(".en")
        return "large" if name.startswith("large") else name

    @property
    def active_device(self) -> str | None:
        return self._active_device

    def _select_device(self, torch: Any) -> str:
        requested = self.requested_device
        if requested == "cpu":
            return "cpu"
        if not torch.cuda.is_available():
            return "cpu"

        if requested.startswith("cuda:"):
            index = int(requested.split(":", 1)[1])
            return requested if index < torch.cuda.device_count() else "cpu"

        required_mb = int(
            os.getenv(
                "TRANSCRIBER_MIN_FREE_VRAM_MB",
                str(self.MODEL_VRAM_MB[self._model_family()]),
            )
        )
        candidates: list[tuple[int, int]] = []
        for index in range(torch.cuda.device_count()):
            try:
                free_bytes, _ = torch.cuda.mem_get_info(index)
                candidates.append((int(free_bytes), index))
            except Exception:
                continue
        if not candidates:
            return "cpu"
        free_bytes, index = max(candidates)
        return f"cuda:{index}" if free_bytes >= required_mb * 1024 * 1024 else "cpu"

    def _load(self, device: str) -> tuple[Any, str]:
        try:
            import torch
            import whisper
        except ImportError as exc:
            raise TranscriptionError("The local Whisper runtime is not installed.") from exc

        try:
            model = whisper.load_model(
                self.model_name,
                device=device,
                download_root=str(self.cache_dir),
            )
            return model, device
        except Exception as exc:
            if not device.startswith("cuda"):
                raise TranscriptionError("The Whisper model could not be loaded.") from exc
            # A busy/incompatible GPU should not make the local service unusable.
            try:
                torch.cuda.empty_cache()
                model = whisper.load_model(
                    self.model_name,
                    device="cpu",
                    download_root=str(self.cache_dir),
                )
                return model, "cpu"
            except Exception as cpu_exc:
                raise TranscriptionError("The Whisper model could not be loaded.") from cpu_exc

    def _ensure_model(self) -> tuple[Any, str]:
        if self._model is not None and self._active_device is not None:
            return self._model, self._active_device
        try:
            import torch
        except ImportError as exc:
            raise TranscriptionError("The local Whisper runtime is not installed.") from exc
        device = self._select_device(torch)
        self._model, self._active_device = self._load(device)
        return self._model, self._active_device

    def transcribe(self, wav_path: Path) -> str:
        # Only one model call occurs at a time. The input audio is data; it is
        # never interpolated into a prompt or treated as an instruction.
        with self._lock:
            model, device = self._ensure_model()
            options: dict[str, Any] = {
                "task": "transcribe",
                "fp16": device.startswith("cuda"),
                "verbose": False,
            }
            if self.language:
                options["language"] = self.language
            try:
                result = model.transcribe(str(wav_path), **options)
            except Exception as exc:
                if not device.startswith("cuda"):
                    raise TranscriptionError("Whisper could not transcribe this recording.") from exc
                result = self._retry_on_cpu(wav_path, options, exc)

        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str):
            raise TranscriptionError("Whisper returned an invalid transcription result.")
        return text.strip()

    def _retry_on_cpu(
        self, wav_path: Path, options: dict[str, Any], original_error: Exception
    ) -> dict[str, Any]:
        try:
            import torch

            self._model = None
            self._active_device = None
            gc.collect()
            torch.cuda.empty_cache()
            self._model, self._active_device = self._load("cpu")
            cpu_options = dict(options, fp16=False)
            return self._model.transcribe(str(wav_path), **cpu_options)
        except Exception as exc:
            raise TranscriptionError("Whisper could not transcribe this recording.") from exc
