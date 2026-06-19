from pathlib import Path

from ncaster.transcribe import format_timestamp, write_transcript


def test_format_timestamp_srt():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(3661.5) == "01:01:01,500"


def test_format_timestamp_vtt_uses_dot():
    assert format_timestamp(1.25, vtt=True) == "00:00:01.250"


SEGMENTS = [
    {"start": 0.0, "end": 1.5, "text": " Hello world"},
    {"start": 1.5, "end": 3.0, "text": " second line "},
]
INFO = {"language": "en", "language_probability": 0.99, "duration": 3.0}


def test_write_srt(tmp_path: Path):
    dst = tmp_path / "out.srt"
    write_transcript(SEGMENTS, INFO, dst, "srt")
    text = dst.read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello world" in text
    assert "2\n00:00:01,500 --> 00:00:03,000\nsecond line" in text


def test_write_vtt(tmp_path: Path):
    dst = tmp_path / "out.vtt"
    write_transcript(SEGMENTS, INFO, dst, "vtt")
    text = dst.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in text


def test_write_txt(tmp_path: Path):
    dst = tmp_path / "out.txt"
    write_transcript(SEGMENTS, INFO, dst, "txt")
    assert dst.read_text(encoding="utf-8") == "Hello world\nsecond line\n"


def test_write_json(tmp_path: Path):
    import json
    dst = tmp_path / "out.json"
    write_transcript(SEGMENTS, INFO, dst, "json")
    data = json.loads(dst.read_text(encoding="utf-8"))
    assert data["language"] == "en"
    assert len(data["segments"]) == 2
