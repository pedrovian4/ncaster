"""Source analysis used to decide edits: word-level transcript, silences, and
filler words. Everything runs locally (Whisper + FFmpeg)."""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path

from ..transcribe import (
    extract_audio_wav,
    find_existing_transcripts,
    get_whisper_model,
    parse_transcript,
)

WORDS_SUFFIX = ".words.json"

# Hesitations that are almost always safe to cut. Ambiguous words that are also
# real vocabulary ("tipo", "like", "so") are reported separately as "maybe".
FILLERS = {
    "pt": {"sure": {"é", "éé", "ééé", "eh", "ehh", "ah", "ahn", "ã", "ãh", "ãã", "hum", "humm",
                    "hmm", "uhm", "uh", "hã", "mm"},
           "maybe": {"tipo", "né", "então", "assim", "sabe", "entendeu", "basicamente"}},
    "en": {"sure": {"uh", "um", "umm", "uhm", "er", "erm", "ah", "eh", "hmm", "mm", "mhm"},
           "maybe": {"like", "so", "basically", "actually", "you know", "i mean", "right"}},
}


def words_path(source: Path) -> Path:
    return source.with_name(source.stem + WORDS_SUFFIX)


def _norm(word: str) -> str:
    w = unicodedata.normalize("NFC", word.strip().lower())
    return re.sub(r"^[^\w]+|[^\w]+$", "", w)


def load_words(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if data.get("segments") else None


def transcribe_words(source: Path, language: str = "auto", model_size: str = "small",
                     force: bool = False) -> tuple[dict, Path]:
    """Word-timestamped transcript of ``source``, cached next to it as
    ``<stem>.words.json``. Returns ``(data, path)``."""
    source = Path(source).expanduser().resolve()
    dst = words_path(source)
    if not force:
        cached = load_words(dst)
        if cached is not None:
            return cached, dst

    wav = extract_audio_wav(source)
    if wav is None:
        raise RuntimeError(f"could not extract audio from {source.name}")
    try:
        model = get_whisper_model(model_size)
        seg_iter, info = model.transcribe(
            str(wav), language=None if language == "auto" else language,
            word_timestamps=True, vad_filter=False,
        )
        segments = []
        for seg in seg_iter:
            words = [
                {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()}
                for w in (seg.words or []) if w.word.strip()
            ]
            segments.append({"start": round(seg.start, 3), "end": round(seg.end, 3),
                             "text": seg.text.strip(), "words": words})
    finally:
        wav.unlink(missing_ok=True)

    data = {"language": info.language, "model": model_size, "duration": info.duration,
            "segments": segments}
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data, dst


def phrase_only_transcript(source: Path) -> dict | None:
    """Fallback: segment-level transcript from an existing json/srt/vtt (no words)."""
    for p in find_existing_transcripts(source.stem, source.parent):
        parsed = parse_transcript(p)
        if parsed:
            return {"language": parsed["language"], "segments": [
                {**s, "text": s["text"].strip(), "words": []} for s in parsed["segments"]
            ]}
    return None


def all_words(data: dict) -> list[dict]:
    return [w for s in data.get("segments", []) for w in s.get("words", [])]


def detect_silences(source: Path, min_silence: float = 0.6, threshold_db: float = -35.0,
                    padding: float = 0.12) -> list[dict]:
    """Silent stretches (via ffmpeg ``silencedetect``), shrunk by ``padding`` on
    each side so cuts don't clip the start/end of words."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(source), "-vn",
         "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return parse_silencedetect(r.stderr, padding)


def parse_silencedetect(stderr: str, padding: float = 0.12) -> list[dict]:
    out, start = [], None
    for line in stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            s, e = start + padding, float(m.group(1)) - padding
            if e - s > 0.05:
                out.append({"start": round(s, 3), "end": round(e, 3), "reason": "silence"})
            start = None
    return out


def find_fillers(data: dict, language: str | None = None, include_maybe: bool = False,
                 pad: float = 0.03) -> list[dict]:
    """Filler words found in a word-level transcript, as candidate cuts."""
    lang = (language or data.get("language") or "pt")[:2]
    table = FILLERS.get(lang, FILLERS["pt"])
    sure, maybe = table["sure"], table["maybe"]
    words = all_words(data)
    out = []
    for i, w in enumerate(words):
        n = _norm(w["word"])
        two = f"{n} {_norm(words[i + 1]['word'])}" if i + 1 < len(words) else ""
        if n in sure:
            kind, end, label = "sure", w["end"], n
        elif include_maybe and n in maybe:
            kind, end, label = "maybe", w["end"], n
        elif include_maybe and two in maybe:
            kind, end, label = "maybe", words[i + 1]["end"], two
        else:
            continue
        out.append({"start": round(max(0.0, w["start"] - pad), 3), "end": round(end + pad, 3),
                    "reason": f"filler:{label}", "confidence": kind})
    return out


def transcript_lines(data: dict, start: float | None = None, end: float | None = None,
                     words: bool = False) -> str:
    """Compact text view of the transcript (source seconds), for LLM consumption."""
    lines = []
    for s in data.get("segments", []):
        if start is not None and s["end"] < start:
            continue
        if end is not None and s["start"] > end:
            continue
        if words and s.get("words"):
            ws = " ".join(f"{w['word']}@{w['start']:.2f}-{w['end']:.2f}" for w in s["words"])
            lines.append(f"[{s['start']:.2f}-{s['end']:.2f}] {ws}")
        else:
            lines.append(f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}")
    return "\n".join(lines)
