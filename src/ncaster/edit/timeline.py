"""The edit timeline: a declarative ``*.ncedit.json`` describing how to turn one
source video into an edited output.

Every time stored in the timeline is in **source seconds**. Cuts remove source
ranges; overlays and subtitles are anchored to source time, so they stay glued
to the words they belong to no matter how the cuts change. Output time is only
computed at render/preview time via :func:`src_to_out` / :func:`out_to_src`.

Mutations go through :func:`save`, which appends the previous state to a
sidecar ``*.ncedit.history.jsonl`` so any change can be undone.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

SCHEMA_VERSION = 1
SUFFIX = ".ncedit.json"
HISTORY_SUFFIX = ".ncedit.history.jsonl"

# Kept ranges shorter than this are dropped (they'd be a flash frame).
MIN_KEEP = 0.08
# Default target length of a preview part, in output seconds.
PART_SECONDS = 15.0

ASPECTS = {"source": None, "9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:5": (4, 5)}
REFRAME_MODES = ("crop", "blur")
SUB_STYLES = ("words", "phrase")
SUB_POSITIONS = ("bottom", "center", "top")
OVERLAY_LAYOUTS = ("full", "top", "pip")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

DEFAULT_SUBTITLES = {
    "enabled": False,
    "style": "words",       # words: few words at a time, current one highlighted
    "max_words": 3,
    "font": "DejaVu Sans",
    "font_size": 0,         # 0 = auto (relative to output height)
    "position": "bottom",
    "uppercase": False,
    "color": "#FFFFFF",
    "highlight": "#FFE600",
}


class TimelineError(ValueError):
    """Invalid timeline operation (bad range, unknown id, …)."""


# --------------------------------------------------------------------------
# Probing / creation
# --------------------------------------------------------------------------

def probe_video(path: Path) -> dict:
    """Duration, size, fps and audio presence of ``path`` via ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,r_frame_rate:stream_side_data=rotation",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0:
        raise TimelineError(f"ffprobe failed on {path}: {r.stderr.strip()[-200:]}")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise TimelineError(f"{path.name} has no video stream")
    num, _, den = (video.get("r_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    if not 1 <= fps <= 240:
        fps = 30.0
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    # Phone footage stores portrait as landscape + rotation; ffmpeg autorotates
    # on decode, so the frames we filter have the swapped size.
    rotation = next((abs(int(sd.get("rotation", 0))) for sd in video.get("side_data_list", [])
                     if "rotation" in sd), 0)
    if rotation % 180 == 90:
        width, height = height, width
    return {
        "duration": float(data.get("format", {}).get("duration") or 0.0),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def default_path(source: Path) -> Path:
    return source.with_name(source.stem + SUFFIX)


def new_timeline(source: Path, aspect: str = "9:16", reframe: str = "crop",
                 info: dict | None = None) -> dict:
    source = source.expanduser().resolve()
    info = info or probe_video(source)
    tl = {
        "version": SCHEMA_VERSION,
        "source": str(source),
        "duration": info["duration"],
        "width": info["width"],
        "height": info["height"],
        "fps": info["fps"],
        "has_audio": info["has_audio"],
        "transcript": None,
        "cuts": [],
        "reframe": {"aspect": aspect, "mode": reframe, "x": 0.5},
        "subtitles": dict(DEFAULT_SUBTITLES),
        "overlays": [],
        "output": {"format": "mp4", "quality": "high"},
    }
    validate(tl)
    return tl


# --------------------------------------------------------------------------
# Persistence + undo
# --------------------------------------------------------------------------

def history_path(path: Path) -> Path:
    return path.with_name(path.name.removesuffix(SUFFIX) + HISTORY_SUFFIX)


PROJECT_FILE = "project.json"


def find_project_root(path: Path) -> Path | None:
    """The ncaster project directory containing ``path``, if any."""
    p = Path(path).expanduser().resolve()
    for d in (p, *p.parents) if p.is_dir() else p.parents:
        if (d / PROJECT_FILE).is_file():
            return d
    return None


def _map_paths(tl: dict, fn) -> dict:
    """Apply ``fn`` to every file path stored in the timeline (returns a copy)."""
    tl = copy.deepcopy(tl)
    for key in ("source", "transcript"):
        if tl.get(key):
            tl[key] = fn(tl[key])
    for ov in tl.get("overlays", []):
        ov["media"] = fn(ov["media"])
    return tl


def _from_disk(tl: dict, path: Path) -> dict:
    """Relative paths on disk are relative to the timeline's directory."""
    base = path.parent.resolve()
    return _map_paths(tl, lambda v: str(Path(v) if Path(v).is_absolute() else (base / v).resolve()))


def _to_disk(tl: dict, path: Path) -> dict:
    """Store files inside the same project (or the timeline's own folder) as
    relative paths, so a project folder can be moved or renamed freely."""
    base = path.parent.resolve()
    root = find_project_root(base) or base

    def rel(v: str) -> str:
        p = Path(v) if Path(v).is_absolute() else (base / v)
        p = p.resolve()
        return os.path.relpath(p, base) if p.is_relative_to(root) else str(p)
    return _map_paths(tl, rel)


def load(path: Path) -> dict:
    path = Path(path).expanduser()
    if not path.is_file():
        raise TimelineError(f"timeline not found: {path}")
    tl = _from_disk(json.loads(path.read_text(encoding="utf-8")), path)
    tl.setdefault("subtitles", {})
    tl["subtitles"] = {**DEFAULT_SUBTITLES, **tl["subtitles"]}
    tl.setdefault("cuts", [])
    tl.setdefault("overlays", [])
    tl.setdefault("output", {"format": "mp4", "quality": "high"})
    validate(tl)
    return tl


def save(path: Path, tl: dict, record_history: bool = True) -> None:
    """Write ``tl``; push the previous on-disk state onto the undo history."""
    path = Path(path).expanduser()
    validate(tl)
    tl["cuts"] = normalize_cuts(tl["cuts"], tl["duration"])
    disk = _to_disk(tl, path)
    if record_history and path.is_file():
        prev = path.read_text(encoding="utf-8")
        if prev.strip() and json.loads(prev) != disk:
            with history_path(path).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(json.loads(prev), ensure_ascii=False) + "\n")
    path.write_text(json.dumps(disk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def undo(path: Path) -> dict:
    """Restore the previous state. Raises if there is nothing to undo."""
    path = Path(path).expanduser()
    hist = history_path(path)
    lines = hist.read_text(encoding="utf-8").splitlines() if hist.is_file() else []
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        raise TimelineError("nothing to undo")
    prev = _from_disk(json.loads(lines.pop()), path)
    hist.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
    save(path, prev, record_history=False)
    return prev


def relocate(path: Path, old_dir: Path, old_to_new: dict[str, str]) -> None:
    """Rewrite file references after a timeline moved from ``old_dir`` to ``path``
    (and its media per ``old_to_new``, absolute → absolute). Covers undo history."""
    path, old_dir = Path(path), Path(old_dir).resolve()

    def remap(v: str) -> str:
        p = str(Path(v) if Path(v).is_absolute() else (old_dir / v).resolve())
        return old_to_new.get(p, p)

    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(_to_disk(_map_paths(raw, remap), path), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    hist = history_path(path)
    if hist.is_file():
        out = [json.dumps(_to_disk(_map_paths(json.loads(ln), remap), path), ensure_ascii=False)
               for ln in hist.read_text(encoding="utf-8").splitlines() if ln.strip()]
        hist.write_text("".join(ln + "\n" for ln in out), encoding="utf-8")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate(tl: dict) -> None:
    rf = tl.get("reframe", {})
    if rf.get("aspect") not in ASPECTS:
        raise TimelineError(f"aspect must be one of {list(ASPECTS)}")
    if rf.get("mode") not in REFRAME_MODES:
        raise TimelineError(f"reframe mode must be one of {list(REFRAME_MODES)}")
    if not 0.0 <= float(rf.get("x", 0.5)) <= 1.0:
        raise TimelineError("reframe x must be between 0 and 1")
    sub = tl.get("subtitles", {})
    if sub.get("style", "words") not in SUB_STYLES:
        raise TimelineError(f"subtitle style must be one of {list(SUB_STYLES)}")
    if sub.get("position", "bottom") not in SUB_POSITIONS:
        raise TimelineError(f"subtitle position must be one of {list(SUB_POSITIONS)}")
    for c in tl.get("cuts", []):
        if float(c["end"]) <= float(c["start"]):
            raise TimelineError(f"cut end must be after start: {c}")
    for o in tl.get("overlays", []):
        if o.get("layout", "full") not in OVERLAY_LAYOUTS:
            raise TimelineError(f"overlay layout must be one of {list(OVERLAY_LAYOUTS)}")
        if float(o["end"]) <= float(o["start"]):
            raise TimelineError(f"overlay end must be after start: {o.get('id')}")


# --------------------------------------------------------------------------
# Cuts
# --------------------------------------------------------------------------

def normalize_cuts(cuts: list[dict], duration: float) -> list[dict]:
    """Clamp to the source, sort, and merge overlapping/adjacent cuts."""
    clean = []
    for c in cuts:
        s = max(0.0, float(c["start"]))
        e = min(float(duration), float(c["end"])) if duration else float(c["end"])
        if e - s > 1e-3:
            clean.append({"start": round(s, 3), "end": round(e, 3),
                          "reason": c.get("reason", "")})
    clean.sort(key=lambda c: c["start"])
    merged: list[dict] = []
    for c in clean:
        if merged and c["start"] <= merged[-1]["end"] + 1e-3:
            last = merged[-1]
            last["end"] = max(last["end"], c["end"])
            if c["reason"] and c["reason"] not in last["reason"].split(", "):
                last["reason"] = ", ".join(r for r in (last["reason"], c["reason"]) if r)
        else:
            merged.append(dict(c))
    return merged


def add_cuts(tl: dict, ranges: list[dict]) -> dict:
    tl = copy.deepcopy(tl)
    for r in ranges:
        if float(r["end"]) <= float(r["start"]):
            raise TimelineError(f"cut end must be after start: {r}")
    tl["cuts"] = normalize_cuts(tl["cuts"] + list(ranges), tl["duration"])
    return tl


def remove_cuts(tl: dict, start: float, end: float) -> dict:
    """Restore source material in [start, end] (splits cuts that straddle it)."""
    tl = copy.deepcopy(tl)
    out = []
    for c in tl["cuts"]:
        if c["end"] <= start or c["start"] >= end:
            out.append(c)
            continue
        if c["start"] < start:
            out.append({**c, "end": start})
        if c["end"] > end:
            out.append({**c, "start": end})
    tl["cuts"] = normalize_cuts(out, tl["duration"])
    return tl


# --------------------------------------------------------------------------
# Time mapping
# --------------------------------------------------------------------------

def kept_ranges(tl: dict) -> list[tuple[float, float]]:
    """Source ranges that survive the cuts, in order."""
    keep, t = [], 0.0
    for c in normalize_cuts(tl["cuts"], tl["duration"]):
        if c["start"] - t >= MIN_KEEP:
            keep.append((t, c["start"]))
        t = max(t, c["end"])
    if tl["duration"] - t >= MIN_KEEP:
        keep.append((t, tl["duration"]))
    return keep


def out_duration(tl: dict) -> float:
    return sum(e - s for s, e in kept_ranges(tl))


def src_to_out(tl: dict, t: float, ranges=None) -> float:
    """Output time for source time ``t``; inside a cut it snaps to the cut point."""
    acc = 0.0
    for s, e in ranges if ranges is not None else kept_ranges(tl):
        if t < s:
            return acc
        if t <= e:
            return acc + (t - s)
        acc += e - s
    return acc


def out_to_src(tl: dict, t: float, ranges=None) -> float:
    acc = 0.0
    ranges = ranges if ranges is not None else kept_ranges(tl)
    for s, e in ranges:
        if t <= acc + (e - s):
            return s + (t - acc)
        acc += e - s
    return ranges[-1][1] if ranges else 0.0


def window_src_ranges(tl: dict, t0: float, t1: float, ranges=None) -> list[tuple[float, float]]:
    """Source sub-ranges that make up output window [t0, t1]."""
    out, acc = [], 0.0
    for s, e in ranges if ranges is not None else kept_ranges(tl):
        seg_len = e - s
        a, b = max(t0, acc), min(t1, acc + seg_len)
        if b - a > 1e-3:
            out.append((s + (a - acc), s + (b - acc)))
        acc += seg_len
        if acc >= t1:
            break
    return out


def map_interval(tl: dict, start: float, end: float, ranges=None) -> tuple[float, float] | None:
    """Output span covered by source interval [start, end], or None if fully cut."""
    ranges = ranges if ranges is not None else kept_ranges(tl)
    a, b = src_to_out(tl, start, ranges), src_to_out(tl, end, ranges)
    return (a, b) if b - a > 1e-3 else None


# --------------------------------------------------------------------------
# Parts (preview chunks)
# --------------------------------------------------------------------------

def parts(tl: dict, target: float = PART_SECONDS) -> list[tuple[float, float]]:
    """Split the output into ~``target``-second windows, snapping each split to
    the nearest cut boundary (a natural edit point) when one is close."""
    total = out_duration(tl)
    if total <= 0:
        return []
    ranges = kept_ranges(tl)
    boundaries, acc = [], 0.0
    for s, e in ranges[:-1]:
        acc += e - s
        boundaries.append(acc)
    out, t0 = [], 0.0
    while total - t0 > target * 1.5:
        ideal = t0 + target
        near = [b for b in boundaries if abs(b - ideal) <= target * 0.35 and b - t0 >= target * 0.5]
        t1 = min(near, key=lambda b: abs(b - ideal)) if near else ideal
        out.append((round(t0, 3), round(t1, 3)))
        t0 = t1
    out.append((round(t0, 3), round(total, 3)))
    return out


# --------------------------------------------------------------------------
# Overlays / settings
# --------------------------------------------------------------------------

def add_overlay(tl: dict, media: str, start: float, end: float,
                layout: str = "full", note: str = "") -> tuple[dict, dict]:
    media_path = Path(media).expanduser().resolve()
    if not media_path.is_file():
        raise TimelineError(f"overlay media not found: {media}")
    if layout not in OVERLAY_LAYOUTS:
        raise TimelineError(f"layout must be one of {list(OVERLAY_LAYOUTS)}")
    if end <= start:
        raise TimelineError("overlay end must be after start")
    tl = copy.deepcopy(tl)
    used = {o["id"] for o in tl["overlays"]}
    n = len(tl["overlays"]) + 1
    while f"o{n}" in used:
        n += 1
    ov = {"id": f"o{n}", "media": str(media_path), "start": round(float(start), 3),
          "end": round(float(end), 3), "layout": layout, "note": note}
    tl["overlays"].append(ov)
    tl["overlays"].sort(key=lambda o: o["start"])
    return tl, ov


def remove_overlay(tl: dict, overlay_id: str) -> dict:
    tl = copy.deepcopy(tl)
    before = len(tl["overlays"])
    tl["overlays"] = [o for o in tl["overlays"] if o["id"] != overlay_id]
    if len(tl["overlays"]) == before:
        raise TimelineError(f"no overlay with id {overlay_id!r}")
    return tl


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def output_size(tl: dict, height: int | None = None) -> tuple[int, int]:
    """Output frame size. Vertical/square targets render at 1080 wide by default."""
    aspect = ASPECTS[tl["reframe"]["aspect"]]
    sw, sh = tl["width"], tl["height"]
    if aspect is None:
        w, h = sw, sh
    else:
        aw, ah = aspect
        if aw >= ah:
            h = min(sh, 1080) if aw > ah else 1080
            w = h * aw / ah
        else:
            w = 1080
            h = w * ah / aw
    if height:
        w, h = w * height / h, height
    return _even(w), _even(h)


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def summary(tl: dict, target: float = PART_SECONDS) -> dict:
    """Compact, JSON-friendly overview (used by the CLI and the MCP server)."""
    ranges = kept_ranges(tl)
    return {
        "source": tl["source"],
        "source_duration": round(tl["duration"], 3),
        "output_duration": round(out_duration(tl), 3),
        "cuts": tl["cuts"],
        "kept_ranges": [[round(s, 3), round(e, 3)] for s, e in ranges],
        "reframe": tl["reframe"],
        "output_size": list(output_size(tl)),
        "subtitles": tl["subtitles"],
        "overlays": [
            {**o, "output_span": [round(x, 3) for x in (map_interval(tl, o["start"], o["end"], ranges) or ())]}
            for o in tl["overlays"]
        ],
        "transcript": tl.get("transcript"),
        "parts": [{"part": i, "start": s, "end": e} for i, (s, e) in enumerate(parts(tl, target), 1)],
    }
