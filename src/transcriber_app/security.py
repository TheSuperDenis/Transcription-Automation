"""Small, deterministic security helpers for the localhost HTTP boundary."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from werkzeug.utils import secure_filename


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_SAFE_FALLBACK_NAME = "recording"


def request_hostname(host_header: str) -> str | None:
    """Return a normalized hostname, rejecting malformed Host headers."""

    try:
        parsed = urlsplit(f"//{host_header}")
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return None
        hostname = parsed.hostname
        # Accessing port also validates malformed/non-numeric port values.
        _ = parsed.port
    except ValueError:
        return None
    return hostname.lower().strip("[]") if hostname else None


def origin_matches_request(origin: str, host_header: str, request_scheme: str) -> bool:
    """Require a browser Origin to name the exact HTTP origin being served."""

    try:
        parsed = urlsplit(origin)
        request_origin = urlsplit(f"//{host_header}")
        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        request_port = request_origin.port or (443 if request_scheme == "https" else 80)
    except ValueError:
        return False

    if (
        parsed.scheme not in {"http", "https"}
        or parsed.scheme != request_scheme
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return False
    if not parsed.hostname or not request_origin.hostname:
        return False
    return (
        parsed.hostname.lower().strip("[]")
        == request_origin.hostname.lower().strip("[]")
        and origin_port == request_port
    )


def sanitize_filename(filename: str, *, max_length: int = 180) -> str:
    """Produce a cross-platform basename that is also safe on Windows."""

    basename = Path((filename or "").replace("\\", "/")).name
    basename = secure_filename(basename).rstrip(" .")
    if not basename:
        basename = _SAFE_FALLBACK_NAME

    suffix = Path(basename).suffix.lower()
    stem = Path(basename).stem.rstrip(" .") or _SAFE_FALLBACK_NAME
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"

    # Keep output filenames comfortably below common platform limits.
    max_stem = max(1, max_length - len(suffix))
    stem = stem[:max_stem].rstrip(" .") or _SAFE_FALLBACK_NAME
    cleaned = f"{stem}{suffix}"
    # Defense in depth if Werkzeug behavior changes.
    return re.sub(r"[^A-Za-z0-9_.-]", "_", cleaned)
