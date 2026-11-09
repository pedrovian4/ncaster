"""Centralized editing projects.

All projects live under one root (``NCASTER_PROJECTS_DIR``, default
``~/Videos/ncaster``)::

    <root>/<slug>/
        project.json   name + creation date
        media/         source videos (moved in) + their timeline/transcript sidecars
        broll/         images and clips for overlays
        renders/       final outputs

Importing a video **moves** it (and any ncaster sidecars next to it) into
``media/``; timelines store project-relative paths, so a whole project folder
can be renamed or moved.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path

from ..config import MEDIA_EXTENSIONS
from . import timeline as T
from .analysis import WORDS_SUFFIX

ROOT_ENV = "NCASTER_PROJECTS_DIR"
SUBDIRS = ("media", "broll", "renders")
# Sidecars that travel with a source video when it's imported.
SIDECAR_SUFFIXES = (T.SUFFIX, T.HISTORY_SUFFIX, WORDS_SUFFIX, ".json", ".srt", ".vtt", ".txt", ".draft.md")
VIDEO_EXTS = {e for e in MEDIA_EXTENSIONS if e not in
              {".mp3", ".aac", ".flac", ".wav", ".opus", ".ogg", ".m4a", ".wma", ".aiff"}}


class ProjectError(ValueError):
    pass


def _videos_dir() -> Path:
    try:
        out = subprocess.run(["xdg-user-dir", "VIDEOS"], capture_output=True, text=True, timeout=3).stdout.strip()
        if out and Path(out) != Path.home():
            return Path(out)
    except Exception:
        pass
    return Path.home() / "Videos"


def projects_root() -> Path:
    env = os.environ.get(ROOT_ENV, "").strip()
    return Path(env).expanduser() if env else _videos_dir() / "ncaster"


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_-]+", "-", s) or "projeto"


def create_project(name: str) -> Path:
    root = projects_root()
    slug = slugify(name)
    path = root / slug
    if (path / T.PROJECT_FILE).is_file():
        raise ProjectError(f"project already exists: {path}")
    for sub in SUBDIRS:
        (path / sub).mkdir(parents=True, exist_ok=True)
    (path / T.PROJECT_FILE).write_text(json.dumps({
        "name": name.strip() or slug,
        "created": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def all_projects() -> list[Path]:
    root = projects_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / T.PROJECT_FILE).is_file())


def find_project(ref: str | Path) -> Path:
    """Resolve a project by path, slug, or (case-insensitive) name."""
    p = Path(ref).expanduser()
    if p.is_dir():
        found = T.find_project_root(p)
        if found:
            return found
    want = str(ref).strip().lower()
    for proj in all_projects():
        if proj.name == slugify(str(ref)) or read_meta(proj).get("name", "").lower() == want:
            return proj
    matches = [proj for proj in all_projects() if want in proj.name or want in read_meta(proj).get("name", "").lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ProjectError(f"{ref!r} matches several projects: {', '.join(m.name for m in matches)}")
    raise ProjectError(f"no project named {ref!r} in {projects_root()}")


def read_meta(project: Path) -> dict:
    try:
        return json.loads((project / T.PROJECT_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}


def media_files(project: Path) -> list[Path]:
    media = project / "media"
    if not media.is_dir():
        return []
    return sorted(p for p in media.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


def broll_files(project: Path) -> list[Path]:
    broll = project / "broll"
    if not broll.is_dir():
        return []
    return sorted(p for p in broll.rglob("*") if p.is_file()
                  and (p.suffix.lower() in VIDEO_EXTS or T.is_image(p)))


def render_files(project: Path) -> list[Path]:
    r = project / "renders"
    return sorted(r.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True) if r.is_dir() else []


def info(project: Path) -> dict:
    """Overview of one project (for listing, the editor, and MCP)."""
    videos = []
    for v in media_files(project):
        tl_path = T.default_path(v)
        entry = {"video": str(v), "timeline": str(tl_path) if tl_path.is_file() else None}
        if tl_path.is_file():
            try:
                tl = T.load(tl_path)
                entry.update(duration=round(tl["duration"], 1), output_duration=round(T.out_duration(tl), 1),
                             cuts=len(tl["cuts"]), aspect=tl["reframe"]["aspect"])
            except Exception as e:
                entry["error"] = str(e)
        videos.append(entry)
    size = 0
    mtime = (project / T.PROJECT_FILE).stat().st_mtime
    for p in project.rglob("*"):
        if p.is_file():
            st = p.stat()
            size += st.st_size
            mtime = max(mtime, st.st_mtime)
    meta = read_meta(project)
    return {
        "name": meta.get("name", project.name), "slug": project.name, "path": str(project),
        "created": meta.get("created"), "updated": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
        "size_bytes": size, "videos": videos,
        "broll": [str(p.relative_to(project)) for p in broll_files(project)],
        "renders": [str(p) for p in render_files(project)],
    }


def _free_name(directory: Path, name: str) -> Path:
    dst = directory / name
    stem, suffix, n = Path(name).stem, Path(name).suffix, 2
    while dst.exists():
        dst = directory / f"{stem}-{n}{suffix}"
        n += 1
    return dst


def add_media(project: Path, files: list[Path]) -> list[tuple[Path, Path]]:
    """Move videos (plus ncaster sidecars) into ``project/media``. Returns (old, new) pairs."""
    media = project / "media"
    media.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in files:
        src = Path(f).expanduser().resolve()
        if not src.is_file():
            raise ProjectError(f"file not found: {f}")
        if src.parent == media.resolve():
            moved.append((src, src))
            continue
        if src.suffix.lower() not in VIDEO_EXTS:
            raise ProjectError(f"not a video: {src.name}")
        dst = _free_name(media, src.name)
        mapping = {str(src): str(dst)}
        sidecars = []
        for suf in SIDECAR_SUFFIXES:
            side = src.with_name(src.stem + suf)
            if side.is_file():
                side_dst = dst.with_name(dst.stem + suf)
                sidecars.append((side, side_dst))
                mapping[str(side)] = str(side_dst)
        shutil.move(str(src), dst)
        for side, side_dst in sidecars:
            shutil.move(str(side), side_dst)
        tl_path = T.default_path(dst)
        if tl_path.is_file():
            T.relocate(tl_path, src.parent, mapping)
            # The timeline named after a video *is* that video's edit, even if it
            # was pointing somewhere else (copied around, stale absolute path).
            tl = T.load(tl_path)
            words = dst.with_name(dst.stem + WORDS_SUFFIX)
            fixed = {"source": str(dst)}
            if words.is_file():
                fixed["transcript"] = str(words)
            if any(tl.get(k) != v for k, v in fixed.items()):
                tl.update(fixed)
                T.save(tl_path, tl, record_history=False)
        moved.append((src, dst))
    return moved


def default_render_path(source: Path) -> Path:
    """Renders of project media go to ``renders/``; anything else next to the source."""
    source = Path(source)
    root = T.find_project_root(source)
    if root:
        (root / "renders").mkdir(exist_ok=True)
        return root / "renders" / f"{source.stem}_edit.mp4"
    return source.with_name(f"{source.stem}_edit.mp4")
