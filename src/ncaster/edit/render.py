"""Render a timeline (or any output-time window of it) with a single FFmpeg
filter graph: cuts → reframe → overlays → burned subtitles.

Previews and the final render share this code path; a preview is just a window
rendered at low resolution with a fast preset, so what you see is what you get.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Callable

from . import timeline as T
from .analysis import load_words, phrase_only_transcript

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ncaster" / "previews"

FINAL_CRF = {"high": 18, "medium": 23, "low": 28}
AUDIO_FADE = 0.012  # micro-fade at every cut, avoids clicks
OVERLAY_FADE = 0.15


# --------------------------------------------------------------------------
# Subtitles (ASS)
# --------------------------------------------------------------------------

def _ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str, upper: bool) -> str:
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")
    return text.upper() if upper else text


def load_transcript(tl: dict) -> dict | None:
    path = tl.get("transcript")
    if path:
        data = load_words(Path(path))
        if data:
            return data
    return phrase_only_transcript(Path(tl["source"]))


def _chunks(tl: dict, data: dict, max_words: int, ranges) -> list[list[tuple[float, float, str]]]:
    """Group surviving words into on-screen chunks, in output time."""
    chunks: list[list[tuple[float, float, str]]] = []
    for seg in data.get("segments", []):
        words = seg.get("words") or []
        if not words:  # phrase-only transcript: whole segment as one chunk
            span = T.map_interval(tl, seg["start"], seg["end"], ranges)
            if span:
                chunks.append([(span[0], span[1], seg["text"].strip())])
            continue
        cur: list[tuple[float, float, str]] = []
        for w in words:
            span = T.map_interval(tl, w["start"], w["end"], ranges)
            if span is None:  # word was cut: break the chunk there
                if cur:
                    chunks.append(cur)
                    cur = []
                continue
            if cur and (len(cur) >= max_words or span[0] - cur[-1][1] > 0.6):
                chunks.append(cur)
                cur = []
            cur.append((span[0], span[1], w["word"].strip()))
        if cur:
            chunks.append(cur)
    return chunks


def build_ass(tl: dict, data: dict | None, t0: float, t1: float) -> str | None:
    """ASS subtitle file for output window [t0, t1] (times relative to t0)."""
    sub = tl["subtitles"]
    if not sub.get("enabled") or not data:
        return None
    W, H = T.output_size(tl)
    vertical = H > W
    size = int(sub.get("font_size") or round(min(W, H) * (0.085 if vertical else 0.06)))
    align, margin_v = {
        "bottom": (2, int(H * (0.22 if vertical else 0.08))),
        "center": (5, 0),
        "top": (8, int(H * 0.12)),
    }[sub.get("position", "bottom")]
    words_style = sub.get("style", "words") == "words"
    max_words = max(1, int(sub.get("max_words", 3))) if words_style else 8
    color, hl = _ass_color(sub.get("color", "#FFFFFF")), _ass_color(sub.get("highlight", "#FFE600"))
    upper = bool(sub.get("uppercase"))

    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{sub.get('font', 'DejaVu Sans')},{size},{color},{color},&H00000000,"
        f"&H64000000,-1,0,0,0,100,100,0,0,1,{max(2, size // 14)},{max(1, size // 30)},"
        f"{align},{int(W * 0.08)},{int(W * 0.08)},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    ranges = T.kept_ranges(tl)
    chunks = _chunks(tl, data, max_words, ranges)
    events = []
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0][0] if ci + 1 < len(chunks) else chunk[-1][1] + 0.4
        chunk_end = min(chunk[-1][1] + 0.3, next_start)
        if words_style and len(chunk) > 1:
            for wi, (ws, we, _) in enumerate(chunk):
                end = chunk[wi + 1][0] if wi + 1 < len(chunk) else chunk_end
                text = " ".join(
                    f"{{\\1c{hl}&}}{_ass_text(w, upper)}{{\\r}}" if k == wi else _ass_text(w, upper)
                    for k, (_, _, w) in enumerate(chunk)
                )
                events.append((ws, end, text))
        else:
            events.append((chunk[0][0], chunk_end, " ".join(_ass_text(w, upper) for _, _, w in chunk)))

    lines = []
    for s, e, text in events:
        s, e = max(s, t0), min(e, t1)
        if e - s < 0.02:
            continue
        lines.append(f"Dialogue: 0,{_ass_time(s - t0)},{_ass_time(e - t0)},Default,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n" if lines else None


def window_text(tl: dict, data: dict | None, t0: float, t1: float) -> str:
    """What is said (after cuts) in output window [t0, t1]."""
    if not data:
        return ""
    ranges = T.kept_ranges(tl)
    out = []
    for chunk in _chunks(tl, data, 10_000, ranges):
        for s, e, w in chunk:
            if e > t0 and s < t1:
                out.append(w)
    return " ".join(out)


# --------------------------------------------------------------------------
# Filter graph
# --------------------------------------------------------------------------

def _reframe_filter(tl: dict, W: int, H: int) -> str:
    rf = tl["reframe"]
    aspect = T.ASPECTS[rf["aspect"]]
    sw, sh = tl["width"], tl["height"]
    if aspect is None:
        return f"scale={W}:{H},setsar=1"
    if rf["mode"] == "blur":
        return (
            f"split=2[rbg][rfg];"
            f"[rbg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"gblur=sigma={max(10, W // 40)}[rbgb];"
            f"[rfg]scale={W}:{H}:force_original_aspect_ratio=decrease[rfgs];"
            f"[rbgb][rfgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    ar = aspect[0] / aspect[1]
    if sw / sh > ar:
        cw, ch = T._even(sh * ar), sh
        x, y = round((sw - cw) * float(rf.get("x", 0.5))), 0
    else:
        cw, ch = sw, T._even(sw / ar)
        x, y = 0, (sh - ch) // 2
    return f"crop={cw}:{ch}:{x}:{y},scale={W}:{H},setsar=1"


def _overlay_layout(layout: str, W: int, H: int) -> tuple[str, str]:
    """(scale filter, overlay x:y) for an overlay layout."""
    if layout == "top":
        h2 = T._even(H / 2)
        return f"scale={W}:{h2}:force_original_aspect_ratio=increase,crop={W}:{h2}", "0:0"
    if layout == "pip":
        pw, m = T._even(W * 0.42), T._even(W * 0.04)
        return f"scale={pw}:-2", f"{W - pw - m}:{m}"
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", "0:0"


def build_job(tl: dict, t0: float, t1: float, dst: Path, *, preview: bool = False,
              quality: str = "high", ass_name: str | None = None) -> tuple[list[str], str]:
    """Return ``(ffmpeg args without the filter-script flag, filter graph)``."""
    ranges = T.kept_ranges(tl)
    src_parts = T.window_src_ranges(tl, t0, t1, ranges)
    if not src_parts:
        raise T.TimelineError(f"empty window {t0:.2f}–{t1:.2f}s (everything cut?)")
    full_w, full_h = T.output_size(tl)
    W, H = T.output_size(tl, height=640 if full_h > full_w else 432) if preview else (full_w, full_h)
    fps = tl.get("fps") or 30
    has_audio = tl.get("has_audio", True)

    base = max(0.0, src_parts[0][0] - 0.5)
    span = src_parts[-1][1] - base + 0.5
    args = ["ffmpeg", "-y", "-hide_banner", "-ss", f"{base:.3f}", "-t", f"{span:.3f}",
            "-i", tl["source"]]

    g = []
    for i, (s, e) in enumerate(src_parts):
        a, b = s - base, e - base
        g.append(f"[0:v]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            d = b - a
            fade = f",afade=t=in:d={AUDIO_FADE},afade=t=out:st={max(0.0, d - AUDIO_FADE):.4f}:d={AUDIO_FADE}" \
                if d > AUDIO_FADE * 4 else ""
            g.append(f"[0:a]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS{fade}[a{i}]")
    n = len(src_parts)
    pads = "".join(f"[v{i}]" + (f"[a{i}]" if has_audio else "") for i in range(n))
    g.append(f"{pads}concat=n={n}:v=1:a={1 if has_audio else 0}[vc]" + ("[ac]" if has_audio else ""))
    g.append(f"[vc]fps={fps},{_reframe_filter(tl, W, H)}[vr]")
    cur = "vr"

    # Overlays (anchored in source time → output span → clipped to the window)
    k = 1
    for ov in tl.get("overlays", []):
        span_o = T.map_interval(tl, ov["start"], ov["end"], ranges)
        if not span_o:
            continue
        a, b = max(span_o[0], t0), min(span_o[1], t1)
        if b - a < 0.05:
            continue
        ls, le = a - t0, b - t0
        dur = le - ls
        if T.is_image(ov["media"]) and not ov["media"].lower().endswith(".gif"):
            args += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", ov["media"]]
        else:
            args += ["-ss", f"{a - span_o[0]:.3f}", "-t", f"{dur:.3f}", "-i", ov["media"]]
        scale, xy = _overlay_layout(ov.get("layout", "full"), W, H)
        fades = ""
        if dur > OVERLAY_FADE * 3:
            fades = (f",fade=t=in:st={ls:.3f}:d={OVERLAY_FADE}:alpha=1"
                     f",fade=t=out:st={le - OVERLAY_FADE:.3f}:d={OVERLAY_FADE}:alpha=1")
        g.append(f"[{k}:v]fps={fps},{scale},setsar=1,format=rgba,setpts=PTS-STARTPTS+{ls:.3f}/TB{fades}[ov{k}]")
        g.append(f"[{cur}][ov{k}]overlay={xy}:enable='between(t,{ls:.3f},{le:.3f})':eof_action=pass[c{k}]")
        cur = f"c{k}"
        k += 1

    if ass_name:
        g.append(f"[{cur}]subtitles=filename={ass_name}[cs]")
        cur = "cs"
    g.append(f"[{cur}]format=yuv420p[vout]")

    args += ["-map", "[vout]"]
    if has_audio:
        args += ["-map", "[ac]"]
    if preview:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30"]
        if has_audio:
            args += ["-c:a", "aac", "-b:a", "96k"]
    else:
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", str(FINAL_CRF.get(quality, 18))]
        if has_audio:
            args += ["-c:a", "aac", "-b:a", "192k"]
    args += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(dst)]
    return args, ";\n".join(g)


@lru_cache(maxsize=1)
def _ffmpeg_major() -> int:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout
        m = re.search(r"version n?(\d+)", out)
        return int(m.group(1)) if m else 7
    except Exception:
        return 7


def _script_flag(path: Path) -> list[str]:
    return ["-/filter_complex", str(path)] if _ffmpeg_major() >= 7 else ["-filter_complex_script", str(path)]


def render_window(tl: dict, t0: float, t1: float, dst: Path, *, preview: bool = False,
                  quality: str = "high",
                  on_progress: Callable[[float, float], None] | None = None) -> dict:
    """Render output window [t0, t1] of ``tl`` to ``dst``. Returns a result dict."""
    dst = Path(dst).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = t1 - t0
    with tempfile.TemporaryDirectory(prefix="ncaster-edit-") as tmp:
        tmpd = Path(tmp)
        ass = build_ass(tl, load_transcript(tl), t0, t1) if tl["subtitles"].get("enabled") else None
        ass_name = None
        if ass:
            (tmpd / "subs.ass").write_text(ass, encoding="utf-8")
            ass_name = "subs.ass"  # relative: ffmpeg runs with cwd=tmp, no path escaping
        args, graph = build_job(tl, t0, t1, dst, preview=preview, quality=quality, ass_name=ass_name)
        (tmpd / "graph.txt").write_text(graph, encoding="utf-8")
        out_idx = args.index("-map")
        cmd = args[:out_idx] + _script_flag(tmpd / "graph.txt") + args[out_idx:]

        with tempfile.TemporaryFile(mode="w+") as err:
            proc = subprocess.Popen(cmd, cwd=tmpd, stdout=subprocess.PIPE, stderr=err, text=True)
            for line in proc.stdout:
                m = re.match(r"out_time_us=(\d+)", line.strip())
                if m and on_progress:
                    on_progress(min(int(m.group(1)) / 1e6, total), total)
            proc.wait()
            err.seek(0)
            stderr = err.read()
    if proc.returncode != 0:
        tail = "\n".join(ln for ln in stderr.splitlines()[-12:])
        return {"ok": False, "path": str(dst), "error": tail or "ffmpeg failed"}
    if on_progress:
        on_progress(total, total)
    return {"ok": True, "path": str(dst), "start": round(t0, 3), "end": round(t1, 3),
            "duration": round(total, 3)}


# --------------------------------------------------------------------------
# Preview helpers
# --------------------------------------------------------------------------

def preview_path(tl: dict, t0: float, t1: float, label: str) -> Path:
    h = hashlib.sha1()
    h.update(json.dumps(tl, sort_keys=True).encode())
    h.update(f"{t0:.3f}-{t1:.3f}".encode())
    tr = tl.get("transcript")
    if tr and Path(tr).is_file():
        h.update(str(Path(tr).stat().st_mtime_ns).encode())
    stem = Path(tl["source"]).stem
    return CACHE_DIR / f"{stem}-{label}-{h.hexdigest()[:10]}.mp4"


def render_preview(tl: dict, t0: float, t1: float, label: str = "preview",
                   on_progress=None) -> dict:
    dst = preview_path(tl, t0, t1, label)
    if dst.is_file() and dst.stat().st_size > 0:
        return {"ok": True, "path": str(dst), "start": round(t0, 3), "end": round(t1, 3),
                "duration": round(t1 - t0, 3), "cached": True}
    return render_window(tl, t0, t1, dst, preview=True, on_progress=on_progress)


def contact_sheet(video: Path, dst: Path | None = None, cols: int = 4, rows: int = 2,
                  duration: float | None = None) -> Path | None:
    """A grid of evenly spaced frames from ``video`` (JPEG). None on failure."""
    video = Path(video)
    dst = dst or video.with_suffix(".sheet.jpg")
    if duration is None:
        from ..probe import probe_duration
        duration = probe_duration(video) or 1.0
    n = cols * rows
    rate = n / max(duration, 0.1)
    r = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps={rate:.5f},scale=-2:240,tile={cols}x{rows}:padding=4:color=black",
         "-frames:v", "1", "-q:v", "4", str(dst)],
        capture_output=True, text=True,
    )
    return dst if r.returncode == 0 and dst.is_file() else None
