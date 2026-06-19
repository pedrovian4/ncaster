from pathlib import Path

from ncaster.transcribe import (
    find_existing_transcripts,
    parse_transcript,
    write_transcript,
)

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": " First line."},
    {"start": 2.0, "end": 4.5, "text": " Second line."},
]
INFO = {"language": "pt", "language_probability": 0.9, "duration": 4.5}


def test_find_existing_prefers_json(tmp_path: Path):
    (tmp_path / "clip.txt").write_text("x", encoding="utf-8")
    (tmp_path / "clip.srt").write_text("x", encoding="utf-8")
    (tmp_path / "clip.json").write_text("{}", encoding="utf-8")
    found = find_existing_transcripts("clip", tmp_path)
    # json first, txt excluded (no timestamps)
    assert [p.suffix for p in found] == [".json", ".srt"]


def test_roundtrip_json(tmp_path: Path):
    dst = tmp_path / "c.json"
    write_transcript(SEGMENTS, INFO, dst, "json")
    parsed = parse_transcript(dst)
    assert parsed["language"] == "pt"
    assert len(parsed["segments"]) == 2
    assert parsed["segments"][0]["text"].strip() == "First line."


def test_roundtrip_srt(tmp_path: Path):
    dst = tmp_path / "c.srt"
    write_transcript(SEGMENTS, INFO, dst, "srt")
    parsed = parse_transcript(dst)
    assert parsed is not None
    assert len(parsed["segments"]) == 2
    assert parsed["segments"][1]["start"] == 2.0
    assert parsed["segments"][1]["text"] == "Second line."


def test_roundtrip_vtt(tmp_path: Path):
    dst = tmp_path / "c.vtt"
    write_transcript(SEGMENTS, INFO, dst, "vtt")
    parsed = parse_transcript(dst)
    assert parsed is not None
    assert len(parsed["segments"]) == 2
    assert parsed["segments"][0]["end"] == 2.0


def test_parse_unparseable_returns_none(tmp_path: Path):
    bad = tmp_path / "c.json"
    bad.write_text("{not json", encoding="utf-8")
    assert parse_transcript(bad) is None
