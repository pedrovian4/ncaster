"""Thin wrappers around ``ffprobe`` plus human-readable formatting helpers."""

import subprocess
from pathlib import Path


def probe_duration(path: Path) -> float | None:
    """Return media duration in seconds, or None if it can't be determined."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def probe_format_name(path: Path) -> str:
    """Return the container/format name (e.g. ``MOV``), falling back to the suffix."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip().split(",")[0].upper()
    except Exception:
        return path.suffix.lstrip(".").upper()


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(secs: float | None) -> str:
    if secs is None:
        return "?"
    secs = int(secs)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
