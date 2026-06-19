import importlib
from pathlib import Path


def _fresh_library(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    import ncaster.library as lib
    return importlib.reload(lib)


def test_record_convert_metadata_only(monkeypatch, tmp_path):
    lib = _fresh_library(monkeypatch, tmp_path)
    entry = lib.record_convert(Path("clip.mov"), Path("clip.mp4"),
                               "mp4", "high", "slow", 10.0, 6.0)
    assert entry["kind"] == "convert"
    assert entry["stored"] is None  # no file copied for conversions
    entries = lib.load_entries()
    assert len(entries) == 1
    assert entries[0]["meta"]["format"] == "mp4"


def test_record_transcribe_copies_file(monkeypatch, tmp_path):
    lib = _fresh_library(monkeypatch, tmp_path)
    srt = tmp_path / "clip.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    entry = lib.record_transcribe(Path("clip.mp4"), srt, "en", "small", 1)
    assert entry["stored"]
    stored = lib.stored_path(entry)
    assert stored.is_file()
    assert "Hi" in stored.read_text(encoding="utf-8")


def test_resolve_by_row_and_id(monkeypatch, tmp_path):
    lib = _fresh_library(monkeypatch, tmp_path)
    lib.record_convert(Path("a.mov"), Path("a.mp4"), "mp4", "high", "slow", 1, 1)
    lib.record_convert(Path("b.mov"), Path("b.mp4"), "mp4", "high", "slow", 1, 1)
    entries = lib.load_entries()
    # row 1 is the newest (b)
    assert lib.resolve_entry(entries, "1")["source"] == "b.mov"
    assert lib.resolve_entry(entries, "2")["source"] == "a.mov"
    # by id prefix
    target = entries[0]
    assert lib.resolve_entry(entries, target["id"])["source"] == "a.mov"
    assert lib.resolve_entry(entries, "999") is None


def test_load_skips_corrupt_lines(monkeypatch, tmp_path):
    lib = _fresh_library(monkeypatch, tmp_path)
    lib.record_convert(Path("a.mov"), Path("a.mp4"), "mp4", "high", "slow", 1, 1)
    with lib.INDEX_FILE.open("a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    assert len(lib.load_entries()) == 1
