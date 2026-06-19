"""ncaster's memory: a per-user log of conversions, transcriptions, and drafts.

Each operation appends one JSON line to ``history.jsonl``. For text artifacts
(transcripts and editing drafts) a copy is kept under ``library/`` so they can
be reviewed later even if the originals are moved or deleted. Conversions are
recorded as metadata only (no video copy).

Stored under ``$XDG_DATA_HOME/ncaster`` (default ``~/.local/share/ncaster``).
"""

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

DATA_DIR = (
    Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "ncaster"
)
LIBRARY_DIR = DATA_DIR / "library"
INDEX_FILE = DATA_DIR / "history.jsonl"

KIND_ICON = {"convert": "🎞", "transcribe": "📝", "draft": "🎬"}
# Only these kinds carry viewable text content.
VIEWABLE_KINDS = ("transcribe", "draft")


def _new_id(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def record(kind, source=None, output=None, meta=None, store_file=None) -> dict | None:
    """Append a history entry; optionally copy a text artifact into the library.

    Best-effort — failures never propagate to the calling operation.
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        entry_id = _new_id(now)

        stored_name = None
        if store_file is not None:
            src = Path(store_file)
            if src.is_file():
                LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
                dest = LIBRARY_DIR / f"{entry_id}__{src.name}"
                shutil.copy2(src, dest)
                stored_name = dest.name

        entry = {
            "id": entry_id,
            "ts": now.isoformat(timespec="seconds"),
            "kind": kind,
            "source": str(source) if source else None,
            "output": str(output) if output else None,
            "stored": stored_name,
            "meta": meta or {},
        }
        with INDEX_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
    except Exception:
        return None


def load_entries() -> list[dict]:
    if not INDEX_FILE.is_file():
        return []
    entries = []
    for line in INDEX_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries


def stored_path(entry: dict) -> Path | None:
    name = entry.get("stored")
    return (LIBRARY_DIR / name) if name else None


def resolve_entry(entries: list[dict], ref: str) -> dict | None:
    """Resolve an entry by 1-based row number (newest first) or by id prefix."""
    ordered = list(reversed(entries))
    if ref.isdigit():
        idx = int(ref) - 1
        if 0 <= idx < len(ordered):
            return ordered[idx]
        return None
    for e in ordered:
        if e["id"] == ref or e["id"].startswith(ref):
            return e
    return None


# --- Convenience recorders used by the core operations -------------------

def record_convert(src, dst, fmt, quality, speed, src_mb, dst_mb) -> dict | None:
    return record("convert", source=getattr(src, "name", src), output=dst, meta={
        "format": fmt, "quality": quality, "speed": speed,
        "src_mb": round(src_mb, 1), "dst_mb": round(dst_mb, 1),
    })


def record_transcribe(src, dst, language, model, segments) -> dict | None:
    return record("transcribe", source=getattr(src, "name", src), output=dst, meta={
        "language": language, "model": model, "segments": segments,
    }, store_file=dst)


def record_draft(source_name, dst, style, model, language, overlays) -> dict | None:
    return record("draft", source=source_name, output=dst, meta={
        "style": style, "model": model, "language": language, "overlays": overlays,
    }, store_file=dst)
