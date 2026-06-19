"""Local speech-to-text transcription using faster-whisper (optional dependency)."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from .console import console
from .probe import probe_duration

# Loaded models are cached per size for the lifetime of the process.
_WHISPER_MODEL_CACHE: dict = {}


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def warn_missing_whisper() -> None:
    console.print(Panel.fit(
        "[red]Transcription support is not installed.[/]\n\n"
        "Add it with one of:\n"
        "  [cyan]uv tool install \"ncaster[transcribe]\" --reinstall[/]\n"
        "  [cyan]uv tool install \".[transcribe]\" --reinstall[/]  [dim](from a clone)[/]\n\n"
        "[dim]This pulls in faster-whisper (CTranslate2). Models download on first use.[/]",
        border_style="red",
    ))


def extract_audio_wav(src: Path) -> Path | None:
    """Extract mono 16 kHz WAV (what Whisper expects) to a temp file."""
    tmp = Path(tempfile.gettempdir()) / f"ncaster_{os.getpid()}_{src.stem}.wav"
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-f", "wav", str(tmp)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        console.print(f"  [red]Audio extraction failed:[/] {r.stderr[-300:]}")
        return None
    return tmp


def format_timestamp(seconds: float, vtt: bool = False) -> str:
    """Format seconds as ``HH:MM:SS,mmm`` (SRT) or ``HH:MM:SS.mmm`` (VTT)."""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "." if vtt else ","
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_transcript(segments: list[dict], info: dict, dst: Path, fmt: str) -> None:
    """Serialize transcription segments to the requested output format."""
    if fmt == "txt":
        dst.write_text(
            "\n".join(s["text"].strip() for s in segments) + "\n", encoding="utf-8"
        )
    elif fmt == "srt":
        lines = []
        for i, s in enumerate(segments, 1):
            lines += [str(i),
                      f"{format_timestamp(s['start'])} --> {format_timestamp(s['end'])}",
                      s["text"].strip(), ""]
        dst.write_text("\n".join(lines), encoding="utf-8")
    elif fmt == "vtt":
        lines = ["WEBVTT", ""]
        for s in segments:
            lines += [f"{format_timestamp(s['start'], vtt=True)} --> "
                      f"{format_timestamp(s['end'], vtt=True)}",
                      s["text"].strip(), ""]
        dst.write_text("\n".join(lines), encoding="utf-8")
    elif fmt == "json":
        dst.write_text(json.dumps({
            "language": info["language"],
            "language_probability": info["language_probability"],
            "duration": info["duration"],
            "segments": segments,
        }, ensure_ascii=False, indent=2), encoding="utf-8")


def get_whisper_model(model_size: str):
    if model_size not in _WHISPER_MODEL_CACHE:
        from faster_whisper import WhisperModel
        # CPU-friendly defaults; int8 keeps memory and time low.
        _WHISPER_MODEL_CACHE[model_size] = WhisperModel(
            model_size, device="cpu", compute_type="int8"
        )
    return _WHISPER_MODEL_CACHE[model_size]


def run_transcription(src: Path, dst: Path, model_size: str,
                      language: str, fmt: str) -> dict | None:
    """Transcribe one file and write the result.

    Returns ``{"segments": [...], "language": <code>}`` on success (so callers
    can reuse the segments without re-transcribing) or ``None`` on failure.
    """
    wav = extract_audio_wav(src)
    if wav is None:
        return None

    try:
        model = get_whisper_model(model_size)
        total = probe_duration(src)
        lang = None if language == "auto" else language

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=30),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(f"transcribing {src.name}", total=total or 100)
            seg_iter, info = model.transcribe(str(wav), language=lang)
            segments = []
            for seg in seg_iter:
                segments.append({"start": seg.start, "end": seg.end, "text": seg.text})
                if total:
                    progress.update(task, completed=min(seg.end, total))
            progress.update(task, completed=total or 100)

        write_transcript(segments, {
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration": info.duration,
        }, dst, fmt)

        console.print(
            f"  [green]Done[/]  lang=[yellow]{info.language}[/] "
            f"({info.language_probability:.0%})  "
            f"{len(segments)} segments → [green]{dst.name}[/]"
        )
        return {"segments": segments, "language": info.language}
    except Exception as e:
        console.print(f"  [red]Transcription error:[/] {e}")
        return None
    finally:
        try:
            wav.unlink(missing_ok=True)
        except Exception:
            pass


# Formats we can parse back into timed segments, richest first.
REUSABLE_EXTS = ("json", "srt", "vtt")


def find_existing_transcripts(stem: str, directory: Path) -> list[Path]:
    """Return existing transcript files for ``stem`` in ``directory`` (richest first)."""
    return [
        directory / f"{stem}.{ext}"
        for ext in REUSABLE_EXTS
        if (directory / f"{stem}.{ext}").is_file()
    ]


def _parse_ts(value: str) -> float:
    """Parse an SRT/VTT timestamp (``HH:MM:SS,mmm`` or ``HH:MM:SS.mmm``)."""
    value = value.strip().replace(",", ".")
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _parse_srt_vtt(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    segments: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        ts_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ts_idx is None:
            continue  # header or index-only block
        start_raw, _, end_raw = lines[ts_idx].partition("-->")
        try:
            start = _parse_ts(start_raw)
            end = _parse_ts(end_raw.split()[0])  # drop any VTT cue settings
        except ValueError:
            continue
        body = " ".join(lines[ts_idx + 1:]).strip()
        if body:
            segments.append({"start": start, "end": end, "text": body})
    return segments


def parse_transcript(path: Path) -> dict | None:
    """Parse an existing transcript file back into ``{"segments", "language"}``.

    Returns None if the file can't be parsed into timed segments.
    """
    try:
        suffix = path.suffix.lower()
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            segs = [
                {"start": s["start"], "end": s["end"], "text": s["text"]}
                for s in data.get("segments", [])
            ]
            if not segs:
                return None
            return {"segments": segs, "language": data.get("language", "auto")}
        if suffix in (".srt", ".vtt"):
            segs = _parse_srt_vtt(path)
            if not segs:
                return None
            return {"segments": segs, "language": "auto"}
    except Exception:
        return None
    return None
