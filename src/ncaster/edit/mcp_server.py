"""MCP server exposing ncaster's video editor to Claude Code (or any MCP client).

    claude mcp add ncaster -- ncaster mcp        # or: ncaster mcp --install

All state lives in the ``*.ncedit.json`` timeline next to the video, so the
user can watch and tweak the same edit from the terminal (``ncaster edit``)
while Claude works on it. stdout is the MCP pipe: all human-facing output is
redirected to stderr, and previews open in a tmux pane / mpv window instead of
the caller's terminal.
"""

from __future__ import annotations

import functools
import json
import sys
from pathlib import Path

INSTRUCTIONS = """\
ncaster edits a talking-head video through a timeline file (<video>.ncedit.json).
ALL times you pass are SOURCE seconds (positions in the original video), never
output seconds — except `preview(start, end)`, which takes OUTPUT seconds.
Overlays and captions are anchored to source time, so they survive later cuts.

Projects: the user's work is organized in ncaster projects (list_projects).
Each project folder has media/ (source videos), broll/ (images/clips to use as
overlays — look there first) and renders/ (final outputs). When the user names
a project, use project_info to find its videos and b-roll. add_to_project and
create_project MOVE the given videos into the project folder; say so.

Typical workflow:
1. create_edit(source) → makes the timeline (default 9:16 crop + word captions).
2. get_transcript(timeline, words=True) → word timestamps to decide cuts.
3. suggest_cuts(timeline) → silences + filler words; review, then cut(...).
   You can also cut whole sentences/takes (retakes, off-topic) from the transcript.
4. set_reframe / set_subtitles / add_overlay as the user asks.
5. get_edit(timeline) → output duration and preview parts.
6. preview(timeline, part=N) → renders that part, shows it to the user in their
   terminal, and returns a contact sheet so you can check framing/captions.
   Preview part by part and let the user react before moving on.
7. render(timeline) → final MP4. undo(timeline) reverts the last change.
Keep ~0.05–0.15 s of breathing room around cuts so words aren't clipped.
"""


def _server():
    try:
        from mcp.server.mcpserver import Image, MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        return MCPServer("ncaster", instructions=INSTRUCTIONS), Image, ToolError
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP, Image
        from mcp.server.fastmcp.exceptions import ToolError
        return FastMCP("ncaster", instructions=INSTRUCTIONS), Image, ToolError


def build():
    from . import analysis as A
    from . import preview as P
    from . import projects as PR
    from . import render as R
    from . import timeline as T

    server, Image, ToolError = _server()

    class _Tools:
        """``@mcp.tool()`` that reports expected failures as clean tool errors."""

        @staticmethod
        def tool(**kw):
            def deco(fn):
                @functools.wraps(fn)
                def wrapped(*args, **kwargs):
                    try:
                        return fn(*args, **kwargs)
                    except (T.TimelineError, PR.ProjectError, OSError, RuntimeError, ValueError, KeyError) as e:
                        raise ToolError(f"{type(e).__name__}: {e}") from None
                return server.tool(**kw)(wrapped)
            return deco

    mcp = _Tools()

    def _tl_path(timeline: str) -> Path:
        p = Path(timeline).expanduser()
        if not p.name.endswith(T.SUFFIX):
            p = T.default_path(p.resolve())
        if not p.is_file():
            raise T.TimelineError(f"timeline not found: {p} (call create_edit first)")
        return p

    def _dump(obj) -> str:
        return json.dumps(obj, ensure_ascii=False, indent=1)

    def _mutate(timeline: str, fn) -> str:
        p = _tl_path(timeline)
        tl = fn(T.load(p))
        T.save(p, tl)
        s = T.summary(tl)
        return _dump({"timeline": str(p), "output_duration": s["output_duration"],
                      "cuts": len(tl["cuts"]), "parts": len(s["parts"])})

    @mcp.tool()
    def list_projects() -> str:
        """All ncaster editing projects (name, folder, videos, edits, renders),
        most recently updated first."""
        infos = sorted((PR.info(p) for p in PR.all_projects()), key=lambda i: i["updated"], reverse=True)
        return _dump({"root": str(PR.projects_root()), "projects": [
            {"name": i["name"], "path": i["path"], "updated": i["updated"],
             "videos": len(i["videos"]), "edited": sum(1 for v in i["videos"] if v.get("timeline")),
             "renders": len(i["renders"]), "broll": len(i["broll"])} for i in infos]})

    @mcp.tool()
    def project_info(project: str) -> str:
        """Details of one project (by name, slug or path): its videos with their
        timeline/edit status, the b-roll files available for overlays, and renders."""
        return _dump(PR.info(PR.find_project(project)))

    @mcp.tool()
    def create_project(name: str, videos: list[str] | None = None) -> str:
        """Create a project and MOVE the given videos into its media/ folder
        (their timelines/transcripts come along). Returns the project details."""
        path = PR.create_project(name)
        moved = PR.add_media(path, [Path(v) for v in videos or []])
        return _dump({"moved": [[str(a), str(b)] for a, b in moved], **PR.info(path)})

    @mcp.tool()
    def add_to_project(project: str, videos: list[str]) -> str:
        """MOVE videos into an existing project's media/ folder. Use the new
        paths returned here from now on."""
        path = PR.find_project(project)
        moved = PR.add_media(path, [Path(v) for v in videos])
        return _dump({"moved": [[str(a), str(b)] for a, b in moved], "project": str(path)})

    @mcp.tool()
    def media_info(path: str) -> str:
        """Duration, resolution, fps and audio presence of a media file."""
        return _dump(T.probe_video(Path(path).expanduser()))

    @mcp.tool()
    def create_edit(source: str, aspect: str = "9:16", reframe: str = "crop",
                    subtitles: bool = True, overwrite: bool = False) -> str:
        """Create the timeline for a source video. aspect: source|9:16|16:9|1:1|4:5.
        reframe: crop (fill frame) | blur (fit with blurred background).
        Returns the timeline path to pass to the other tools."""
        src = Path(source).expanduser().resolve()
        p = T.default_path(src)
        if p.is_file() and not overwrite:
            return _dump({"timeline": str(p), "note": "already exists; reusing it",
                          **T.summary(T.load(p))})
        tl = T.new_timeline(src, aspect=aspect, reframe=reframe)
        tl["subtitles"]["enabled"] = subtitles
        cached = A.words_path(src)
        if A.load_words(cached):
            tl["transcript"] = str(cached)
        T.save(p, tl, record_history=False)
        return _dump({"timeline": str(p), **T.summary(tl)})

    @mcp.tool()
    def get_edit(timeline: str) -> str:
        """Current timeline: cuts, kept ranges, output duration, settings, overlays,
        and the preview parts (output-time windows) with what is said in each."""
        p = _tl_path(timeline)
        tl = T.load(p)
        s = T.summary(tl)
        data = R.load_transcript(tl)
        for part in s["parts"]:
            part["said"] = R.window_text(tl, data, part["start"], part["end"])
        return _dump({"timeline": str(p), **s})

    @mcp.tool()
    def get_transcript(timeline: str, words: bool = False, language: str = "auto",
                       model: str = "small", start: float | None = None,
                       end: float | None = None) -> str:
        """Transcript of the source (transcribes locally with Whisper on first call,
        then cached). Lines are `[start-end] text` in SOURCE seconds; with
        words=True each word is `word@start-end`. Optionally limit to [start, end]."""
        p = _tl_path(timeline)
        tl = T.load(p)
        data = A.load_words(Path(tl["transcript"])) if tl.get("transcript") else None
        if data is None:
            data, wpath = A.transcribe_words(Path(tl["source"]), language, model)
            tl["transcript"] = str(wpath)
            T.save(p, tl)
        return f"language={data.get('language')}\n" + A.transcript_lines(data, start, end, words)

    @mcp.tool()
    def suggest_cuts(timeline: str, silences: bool = True, fillers: bool = True,
                     include_maybe_fillers: bool = False, min_silence: float = 0.6,
                     threshold_db: float = -35.0) -> str:
        """Candidate cuts (SOURCE seconds): silent gaps and hesitation words
        ("é", "ahn", "um"…). Ambiguous fillers ("tipo", "né", "like") only with
        include_maybe_fillers. Nothing is applied — pass what you keep to cut()."""
        p = _tl_path(timeline)
        tl = T.load(p)
        out = []
        if silences:
            out += A.detect_silences(Path(tl["source"]), min_silence, threshold_db)
        if fillers:
            data = A.load_words(Path(tl["transcript"])) if tl.get("transcript") else None
            if data is None:
                data, wpath = A.transcribe_words(Path(tl["source"]))
                tl["transcript"] = str(wpath)
                T.save(p, tl)
            out += A.find_fillers(data, include_maybe=include_maybe_fillers)
        out.sort(key=lambda c: c["start"])
        return _dump({"count": len(out), "total_seconds": round(sum(c["end"] - c["start"] for c in out), 2),
                      "cuts": out})

    @mcp.tool()
    def cut(timeline: str, ranges: list[dict]) -> str:
        """Remove SOURCE ranges. ranges = [{"start": s, "end": e, "reason": "..."}].
        Overlapping cuts are merged."""
        return _mutate(timeline, lambda tl: T.add_cuts(tl, ranges))

    @mcp.tool()
    def uncut(timeline: str, start: float, end: float) -> str:
        """Restore SOURCE range [start, end] (removes/splits cuts inside it)."""
        return _mutate(timeline, lambda tl: T.remove_cuts(tl, start, end))

    @mcp.tool()
    def set_reframe(timeline: str, aspect: str = "9:16", mode: str = "crop", x: float = 0.5) -> str:
        """Output framing. aspect: source|9:16|16:9|1:1|4:5. mode: crop|blur.
        x: horizontal crop center 0 (left) … 1 (right), to keep the speaker in frame."""
        def fn(tl):
            tl["reframe"] = {"aspect": aspect, "mode": mode, "x": x}
            return tl
        return _mutate(timeline, fn)

    @mcp.tool()
    def set_subtitles(timeline: str, enabled: bool = True, style: str | None = None,
                      max_words: int | None = None, position: str | None = None,
                      uppercase: bool | None = None, font_size: int | None = None,
                      color: str | None = None, highlight: str | None = None,
                      font: str | None = None) -> str:
        """Burned-in captions. style: words (few words, current one highlighted) |
        phrase. position: bottom|center|top. font_size in output pixels (0 = auto).
        Colors as #RRGGBB. Needs a transcript (get_transcript)."""
        def fn(tl):
            upd = {k: v for k, v in dict(style=style, max_words=max_words, position=position,
                                         uppercase=uppercase, font_size=font_size, color=color,
                                         highlight=highlight, font=font).items() if v is not None}
            tl["subtitles"].update(enabled=enabled, **upd)
            return tl
        return _mutate(timeline, fn)

    @mcp.tool()
    def add_overlay(timeline: str, media: str, start: float, end: float,
                    layout: str = "full", note: str = "") -> str:
        """Show an image or video clip over the speaker from SOURCE start to end.
        layout: full (covers frame) | top (upper half, speaker stays visible below)
        | pip (small, top-right). Returns the overlay id."""
        p = _tl_path(timeline)
        tl, ov = T.add_overlay(T.load(p), media, start, end, layout, note)
        T.save(p, tl)
        return _dump({"overlay": ov, "output_span": T.map_interval(tl, start, end)})

    @mcp.tool()
    def remove_overlay(timeline: str, overlay_id: str) -> str:
        """Remove an overlay by id."""
        return _mutate(timeline, lambda tl: T.remove_overlay(tl, overlay_id))

    @mcp.tool()
    def undo(timeline: str) -> str:
        """Revert the last change to the timeline."""
        p = _tl_path(timeline)
        tl = T.undo(p)
        return _dump({"timeline": str(p), "output_duration": round(T.out_duration(tl), 3),
                      "cuts": len(tl["cuts"])})

    @mcp.tool(structured_output=False)
    def preview(timeline: str, part: int | None = None, start: float | None = None,
                end: float | None = None, show_user: bool = True) -> list:
        """Render a low-res preview of one part (see get_edit) or of an OUTPUT-time
        window [start, end]. With show_user it plays for the user in a tmux pane /
        mpv window. Returns the file path and a contact sheet (8 frames) to inspect."""
        p = _tl_path(timeline)
        tl = T.load(p)
        windows = T.parts(tl)
        if start is not None or end is not None:
            a, b = start or 0.0, end if end is not None else T.out_duration(tl)
            label = f"w{a:.0f}-{b:.0f}"
        else:
            part = part or 1
            if not 1 <= part <= len(windows):
                raise T.TimelineError(f"part must be 1–{len(windows)}")
            a, b = windows[part - 1]
            label = f"p{part:02d}"
        res = R.render_preview(tl, a, b, label)
        if not res["ok"]:
            return [_dump(res)]
        res["said"] = R.window_text(tl, R.load_transcript(tl), a, b)
        if show_user:
            res["shown_in"] = P.open_external(Path(res["path"]))
        out = [_dump(res)]
        sheet = R.contact_sheet(Path(res["path"]), duration=b - a)
        if sheet:
            out.append(Image(path=sheet))
        return out

    @mcp.tool()
    def render(timeline: str, output: str | None = None, quality: str = "high") -> str:
        """Render the final video (H.264/AAC MP4). quality: high|medium|low.
        Default output: <project>/renders/<stem>_edit.mp4 for project media,
        otherwise <stem>_edit.mp4 next to the source."""
        p = _tl_path(timeline)
        tl = T.load(p)
        src = Path(tl["source"])
        dst = Path(output).expanduser() if output else PR.default_render_path(src)
        if dst.resolve() == src.resolve():
            raise T.TimelineError("output would overwrite the source")
        res = R.render_window(tl, 0, T.out_duration(tl), dst, quality=quality)
        if res["ok"]:
            try:
                from .. import library
                library.record("edit", source=src.name, output=dst, meta={
                    "cuts": len(tl["cuts"]), "aspect": tl["reframe"]["aspect"],
                    "subtitles": tl["subtitles"]["enabled"], "overlays": len(tl["overlays"]),
                    "duration": round(T.out_duration(tl), 1)})
            except Exception:
                pass
        return _dump(res)

    return server


def mcp_available() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


def run() -> None:
    from ..console import console
    console.file = sys.stderr  # stdout belongs to the MCP protocol
    build().run()
