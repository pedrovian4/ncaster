import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ncaster.edit import analysis as A
from ncaster.edit import render as R
from ncaster.edit import timeline as T

INFO = {"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}

WORDS = {"language": "pt", "segments": [{
    "start": 0.5, "end": 4.0, "text": "olá pessoal tudo bem é vocês",
    "words": [
        {"start": 0.5, "end": 0.9, "word": "olá"},
        {"start": 0.9, "end": 1.5, "word": "pessoal,"},
        {"start": 1.6, "end": 2.0, "word": "tudo"},
        {"start": 2.0, "end": 2.4, "word": "bem"},
        {"start": 2.5, "end": 3.0, "word": "Éé..."},
        {"start": 3.1, "end": 4.0, "word": "vocês"},
    ],
}]}


def make_tl(tmp_path: Path, **kw) -> dict:
    src = tmp_path / "talk.mp4"
    src.write_bytes(b"")
    return T.new_timeline(src, info=INFO, **kw)


def test_cuts_are_merged_and_clamped(tmp_path):
    tl = make_tl(tmp_path)
    tl = T.add_cuts(tl, [{"start": 2, "end": 3, "reason": "a"}, {"start": 2.5, "end": 4, "reason": "b"},
                         {"start": 9.5, "end": 99}])
    assert tl["cuts"] == [{"start": 2.0, "end": 4.0, "reason": "a, b"},
                          {"start": 9.5, "end": 10.0, "reason": ""}]
    assert T.kept_ranges(tl) == [(0.0, 2.0), (4.0, 9.5)]
    assert T.out_duration(tl) == pytest.approx(7.5)


def test_invalid_cut_rejected(tmp_path):
    with pytest.raises(T.TimelineError):
        T.add_cuts(make_tl(tmp_path), [{"start": 5, "end": 4}])


def test_time_mapping(tmp_path):
    tl = T.add_cuts(make_tl(tmp_path), [{"start": 2, "end": 4}])
    assert T.src_to_out(tl, 1.0) == 1.0
    assert T.src_to_out(tl, 3.0) == 2.0       # inside the cut → snaps to cut point
    assert T.src_to_out(tl, 5.0) == 3.0
    assert T.out_to_src(tl, 3.0) == 5.0
    assert T.map_interval(tl, 2.2, 3.8) is None  # fully cut
    assert T.window_src_ranges(tl, 1.0, 3.0) == [(1.0, 2.0), (4.0, 5.0)]


def test_uncut_splits_a_cut(tmp_path):
    tl = T.add_cuts(make_tl(tmp_path), [{"start": 2, "end": 6, "reason": "x"}])
    tl = T.remove_cuts(tl, 3, 4)
    assert [(c["start"], c["end"]) for c in tl["cuts"]] == [(2.0, 3.0), (4.0, 6.0)]


def test_parts_snap_to_cut_boundaries(tmp_path):
    src = tmp_path / "long.mp4"
    src.write_bytes(b"")
    tl = T.new_timeline(src, info={**INFO, "duration": 60.0})
    tl = T.add_cuts(tl, [{"start": 13.0, "end": 14.0}])  # boundary at output 13.0
    windows = T.parts(tl, target=15.0)
    assert windows[0] == (0.0, 13.0)
    assert windows[-1][1] == pytest.approx(59.0)
    assert all(b > a for a, b in windows)


def test_output_size():
    assert T.output_size({"reframe": {"aspect": "9:16"}, "width": 1920, "height": 1080}) == (1080, 1920)
    assert T.output_size({"reframe": {"aspect": "source"}, "width": 1920, "height": 1080},
                         height=432) == (768, 432)


def test_save_and_undo(tmp_path):
    tl = make_tl(tmp_path)
    path = tmp_path / "talk.ncedit.json"
    T.save(path, tl)
    T.save(path, T.add_cuts(tl, [{"start": 1, "end": 2}]))
    assert len(T.load(path)["cuts"]) == 1
    T.undo(path)
    assert T.load(path)["cuts"] == []
    with pytest.raises(T.TimelineError):
        T.undo(path)


def test_overlay_add_remove(tmp_path):
    img = tmp_path / "b.png"
    img.write_bytes(b"x")
    tl, ov = T.add_overlay(make_tl(tmp_path), str(img), 3, 5, "top")
    assert ov["id"] == "o1" and tl["overlays"][0]["layout"] == "top"
    assert T.remove_overlay(tl, "o1")["overlays"] == []
    with pytest.raises(T.TimelineError):
        T.add_overlay(tl, str(tmp_path / "missing.png"), 1, 2)


def test_find_fillers_normalizes_punctuation():
    cuts = A.find_fillers(WORDS)
    assert len(cuts) == 1 and cuts[0]["reason"] == "filler:éé"
    assert cuts[0]["start"] == pytest.approx(2.47)


def test_find_maybe_fillers_two_words():
    data = {"language": "en", "segments": [{"start": 0, "end": 2, "text": "", "words": [
        {"start": 0.0, "end": 0.3, "word": "you"}, {"start": 0.3, "end": 0.6, "word": "know,"},
        {"start": 0.7, "end": 1.0, "word": "right"}]}]}
    assert A.find_fillers(data) == []
    reasons = [c["reason"] for c in A.find_fillers(data, include_maybe=True)]
    assert reasons == ["filler:you know", "filler:right"]


def test_parse_silencedetect():
    log = ("[silencedetect @ 0x1] silence_start: 1.5\n"
           "[silencedetect @ 0x1] silence_end: 3.0 | silence_duration: 1.5\n"
           "[silencedetect @ 0x1] silence_start: 7.9\n"
           "[silencedetect @ 0x1] silence_end: 8.0 | silence_duration: 0.1\n")
    assert A.parse_silencedetect(log, padding=0.1) == [{"start": 1.6, "end": 2.9, "reason": "silence"}]


def test_ass_drops_cut_words_and_highlights(tmp_path):
    tl = T.add_cuts(make_tl(tmp_path), [{"start": 2.47, "end": 3.03}])
    tl["subtitles"]["enabled"] = True
    ass = R.build_ass(tl, WORDS, 0, T.out_duration(tl))
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Éé" not in ass
    assert "{\\1c&H0000E6FF&}olá{\\r} pessoal, tudo" in ass
    assert R.window_text(tl, WORDS, 0, 10) == "olá pessoal, tudo bem vocês"


def test_ass_disabled_returns_none(tmp_path):
    assert R.build_ass(make_tl(tmp_path), WORDS, 0, 10) is None


def test_build_job_graph(tmp_path):
    img = tmp_path / "b.png"
    img.write_bytes(b"x")
    tl = T.add_cuts(make_tl(tmp_path), [{"start": 2, "end": 4}])
    tl, _ = T.add_overlay(tl, str(img), 5, 6, "pip")
    args, graph = R.build_job(tl, 0, T.out_duration(tl), tmp_path / "o.mp4", ass_name="subs.ass")
    assert "concat=n=2:v=1:a=1" in graph
    assert "crop=608:1080:656:0,scale=1080:1920" in graph
    assert "between(t,3.000,4.000)" in graph          # overlay mapped to output time
    assert "subtitles=filename=subs.ass" in graph
    assert args[args.index("-loop") + 1] == "1"         # still image looped


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_window_end_to_end(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=640x360:r=25:d=4", "-f", "lavfi", "-i", "sine=d=4",
                    "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
    tl = T.add_cuts(T.new_timeline(src), [{"start": 1.0, "end": 2.0}])
    (tmp_path / "src.words.json").write_text(json.dumps(WORDS), encoding="utf-8")
    tl["transcript"] = str(tmp_path / "src.words.json")
    tl["subtitles"]["enabled"] = True
    res = R.render_window(tl, 0, T.out_duration(tl), tmp_path / "out.mp4", preview=True)
    assert res["ok"], res.get("error")
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height",
                          "-of", "json", res["path"]], capture_output=True, text=True).stdout
    info = json.loads(out)
    assert float(info["format"]["duration"]) == pytest.approx(3.0, abs=0.15)
    assert (info["streams"][0]["width"], info["streams"][0]["height"]) == (360, 640)
