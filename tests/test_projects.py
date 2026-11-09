import json
import shutil
from pathlib import Path

import pytest

from ncaster.edit import projects as PR
from ncaster.edit import timeline as T

INFO = {"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "projects"
    monkeypatch.setenv(PR.ROOT_ENV, str(r))
    return r


def loose_video_with_edit(directory: Path) -> Path:
    """A video outside any project, with a timeline, history and transcript."""
    directory.mkdir(parents=True, exist_ok=True)
    video = directory / "take1.mp4"
    video.write_bytes(b"video")
    words = directory / "take1.words.json"
    words.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "oi", "words": []}]}))
    ov = directory / "logo.png"
    ov.write_bytes(b"png")
    tl_path = T.default_path(video)
    tl = T.new_timeline(video, info=INFO)
    tl["transcript"] = str(words)
    T.save(tl_path, tl)
    tl, _ = T.add_overlay(T.add_cuts(tl, [{"start": 1, "end": 2}]), str(ov), 3, 4)
    T.save(tl_path, tl)
    return video


def test_create_and_find(root):
    path = PR.create_project("Lançamento do Curso")
    assert path == root / "lancamento-do-curso"
    assert all((path / d).is_dir() for d in PR.SUBDIRS)
    assert PR.find_project("lançamento do curso") == path
    assert PR.find_project("curso") == path          # unique partial match
    assert PR.find_project(path / "media") == path
    with pytest.raises(PR.ProjectError):
        PR.create_project("lancamento do curso")
    with pytest.raises(PR.ProjectError):
        PR.find_project("nope")


def test_add_media_moves_video_and_sidecars(root, tmp_path):
    video = loose_video_with_edit(tmp_path / "downloads")
    proj = PR.create_project("p")
    [(old, new)] = PR.add_media(proj, [video])

    assert not old.exists() and new == proj / "media" / "take1.mp4" and new.is_file()
    assert (proj / "media" / "take1.words.json").is_file()
    assert not (tmp_path / "downloads" / "take1.ncedit.json").exists()

    tl_path = proj / "media" / "take1.ncedit.json"
    raw = json.loads(tl_path.read_text())
    assert raw["source"] == "take1.mp4"                      # relative inside the project
    assert raw["transcript"] == "take1.words.json"
    assert raw["overlays"][0]["media"] == str(tmp_path / "downloads" / "logo.png")  # outside: absolute

    tl = T.load(tl_path)
    assert tl["source"] == str(new) and len(tl["cuts"]) == 1
    T.undo(tl_path)                                           # history was rewritten too
    assert T.load(tl_path)["source"] == str(new)
    assert T.load(tl_path)["cuts"] == []


def test_name_collision_renames_with_sidecars(root, tmp_path):
    proj = PR.create_project("p")
    PR.add_media(proj, [loose_video_with_edit(tmp_path / "a")])
    [(_, new)] = PR.add_media(proj, [loose_video_with_edit(tmp_path / "b")])
    assert new.name == "take1-2.mp4"
    tl = T.load(proj / "media" / "take1-2.ncedit.json")
    assert tl["source"] == str(new)
    assert tl["transcript"] == str(proj / "media" / "take1-2.words.json")


def test_project_folder_can_be_moved(root, tmp_path):
    proj = PR.create_project("p")
    (proj / "broll" / "cafe.png").write_bytes(b"png")
    [(_, video)] = PR.add_media(proj, [loose_video_with_edit(tmp_path / "dl")])
    tl_path = T.default_path(video)
    tl, _ = T.add_overlay(T.load(tl_path), str(proj / "broll" / "cafe.png"), 5, 6)
    T.save(tl_path, tl)
    assert json.loads(tl_path.read_text())["overlays"][-1]["media"] == "../broll/cafe.png"

    moved = tmp_path / "elsewhere" / "renamed"
    moved.parent.mkdir()
    shutil.move(str(proj), moved)
    tl = T.load(moved / "media" / "take1.ncedit.json")
    assert tl["source"] == str(moved / "media" / "take1.mp4")
    assert tl["overlays"][-1]["media"] == str(moved / "broll" / "cafe.png")


def test_info_and_render_path(root, tmp_path):
    proj = PR.create_project("p")
    (proj / "broll" / "x.mp4").write_bytes(b"v")
    PR.add_media(proj, [loose_video_with_edit(tmp_path / "dl")])
    (proj / "media" / "raw.mov").write_bytes(b"v")
    inf = PR.info(proj)
    assert [Path(v["video"]).name for v in inf["videos"]] == ["raw.mov", "take1.mp4"]
    assert inf["videos"][1]["cuts"] == 1 and inf["videos"][0]["timeline"] is None
    assert inf["broll"] == ["broll/x.mp4"]
    assert PR.default_render_path(proj / "media" / "take1.mp4") == proj / "renders" / "take1_edit.mp4"
    assert PR.default_render_path(tmp_path / "loose.mp4") == tmp_path / "loose_edit.mp4"


def test_rejects_non_video(root, tmp_path):
    proj = PR.create_project("p")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"a")
    with pytest.raises(PR.ProjectError):
        PR.add_media(proj, [audio])
