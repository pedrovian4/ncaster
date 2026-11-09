"""``ncaster edit …`` — terminal front-end for timeline editing.

The same ``*.ncedit.json`` is shared with the MCP server, so you can let Claude
Code edit it and watch/refine the result here (the editor reloads the file on
every action).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import click
import questionary
from rich import box
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from ..console import console
from ..probe import human_duration
from . import analysis as A
from . import preview as P
from . import render as R
from . import timeline as T

Q_STYLE = questionary.Style([("qmark", "fg:cyan bold"), ("pointer", "fg:cyan bold"),
                             ("highlighted", "fg:cyan bold"), ("selected", "fg:green")])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def resolve_timeline(target: str | Path, create: bool = False, **new_kwargs) -> Path:
    """Accept a timeline or a source video; return the timeline path."""
    p = Path(target).expanduser()
    if p.name.endswith(T.SUFFIX):
        if not p.is_file():
            raise click.ClickException(f"timeline not found: {p}")
        return p.resolve()
    if not p.is_file():
        raise click.ClickException(f"file not found: {p}")
    tl_path = T.default_path(p.resolve())
    if tl_path.is_file():
        return tl_path
    if not create:
        raise click.ClickException(f"no timeline for {p.name} yet — run: ncaster edit new {p}")
    T.save(tl_path, T.new_timeline(p, **new_kwargs))
    console.print(f"[green]Created[/] {tl_path}")
    return tl_path


def _progress_render(fn, label: str):
    with Progress(SpinnerColumn(), TextColumn("[bold]{task.description}"), BarColumn(bar_width=30),
                  TaskProgressColumn(), TimeRemainingColumn(), console=console, transient=True) as prog:
        task = prog.add_task(label, total=100)

        def cb(done, total):
            prog.update(task, total=total or 100, completed=done)
        return fn(cb)


def ensure_transcript(tl_path: Path, language: str = "auto", model: str = "small") -> dict | None:
    """Make sure the timeline points at a word-level transcript (transcribe if needed)."""
    tl = T.load(tl_path)
    if tl.get("transcript") and A.load_words(Path(tl["transcript"])):
        return A.load_words(Path(tl["transcript"]))
    src = Path(tl["source"])
    cached = A.load_words(A.words_path(src))
    if cached is None:
        from ..transcribe import faster_whisper_available, warn_missing_whisper
        if not faster_whisper_available():
            warn_missing_whisper()
            return None
        with console.status(f"[bold]Transcribing {src.name} (word timestamps, {model})…"):
            cached, _ = A.transcribe_words(src, language, model)
    tl["transcript"] = str(A.words_path(src))
    T.save(tl_path, tl)
    return cached


def print_overview(tl_path: Path) -> list[tuple[float, float]]:
    tl = T.load(tl_path)
    data = R.load_transcript(tl)
    s = T.summary(tl)
    rf, sub = tl["reframe"], tl["subtitles"]
    saved = tl["duration"] - s["output_duration"]
    console.print(Panel.fit(
        f"[bold cyan]{Path(tl['source']).name}[/]  [dim]{tl_path.name}[/]\n"
        f"{human_duration(tl['duration'])} → [bold]{human_duration(s['output_duration'])}[/]"
        f"  [dim](-{saved:.1f}s, {len(tl['cuts'])} cuts)[/]   "
        f"aspect [yellow]{rf['aspect']}[/] {rf['mode']}   "
        f"{s['output_size'][0]}×{s['output_size'][1]}   "
        f"subs [yellow]{sub['style'] if sub['enabled'] else 'off'}[/]   "
        f"overlays [yellow]{len(tl['overlays'])}[/]"
        + ("" if data else "   [dim]no transcript[/]"),
        border_style="cyan",
    ))
    windows = T.parts(tl)
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, expand=True)
    table.add_column("#", style="bold cyan", justify="right", no_wrap=True, min_width=2)
    table.add_column("time", no_wrap=True, min_width=11)
    table.add_column("dur", justify="right", no_wrap=True, min_width=5)
    table.add_column("ov", justify="right", style="magenta", no_wrap=True, min_width=2)
    table.add_column("said", overflow="ellipsis", no_wrap=True, ratio=1)
    ranges = T.kept_ranges(tl)
    for i, (a, b) in enumerate(windows, 1):
        n_ov = sum(1 for o in tl["overlays"]
                   if (sp := T.map_interval(tl, o["start"], o["end"], ranges)) and sp[0] < b and sp[1] > a)
        table.add_row(str(i), f"{human_duration(a)}–{human_duration(b)}", f"{b - a:.1f}s",
                      str(n_ov or ""), R.window_text(tl, data, a, b) or "[dim]—[/]")
    console.print(table)
    return windows


def preview_part(tl_path: Path, index: int, play: bool = True, loop: bool = False) -> dict:
    tl = T.load(tl_path)
    windows = T.parts(tl)
    if not 1 <= index <= len(windows):
        raise click.ClickException(f"part must be 1–{len(windows)}")
    a, b = windows[index - 1]
    return preview_window(tl, a, b, f"p{index:02d}", play, loop, title=f"part {index}/{len(windows)}")


def preview_window(tl: dict, a: float, b: float, label: str, play: bool = True,
                   loop: bool = False, title: str = "") -> dict:
    res = _progress_render(lambda cb: R.render_preview(tl, a, b, label, on_progress=cb),
                           f"rendering {title or label} ({b - a:.1f}s)")
    if not res["ok"]:
        console.print(f"[red]Preview failed:[/]\n{res['error']}")
        return res
    console.print(f"[green]▶[/] {title or label}  {human_duration(a)}–{human_duration(b)}  "
                  f"[dim]{res['path']}[/]  [dim](q to stop)[/]")
    if play:
        sheet = None
        if not shutil.which("mpv"):
            sheet = R.contact_sheet(Path(res["path"]), duration=b - a)
        if not P.play_in_terminal(Path(res["path"]), sheet, loop):
            console.print("[yellow]Install mpv (or chafa) to watch previews in the terminal.[/]")
    return res


def render_final(tl_path: Path, output: str | None = None, quality: str | None = None) -> dict:
    tl = T.load(tl_path)
    q = quality or tl.get("output", {}).get("quality", "high")
    src = Path(tl["source"])
    from .projects import default_render_path
    dst = Path(output).expanduser() if output else default_render_path(src)
    if dst.resolve() == src.resolve():
        raise click.ClickException("output would overwrite the source")
    total = T.out_duration(tl)
    res = _progress_render(lambda cb: R.render_window(tl, 0, total, dst, quality=q, on_progress=cb),
                           f"rendering {dst.name}")
    if res["ok"]:
        console.print(f"[green]Rendered[/] {dst}  [dim]({human_duration(total)}, {q})[/]")
        try:
            from .. import library
            library.record("edit", source=src.name, output=dst, meta={
                "cuts": len(tl["cuts"]), "aspect": tl["reframe"]["aspect"],
                "subtitles": tl["subtitles"]["enabled"], "overlays": len(tl["overlays"]),
                "duration": round(total, 1),
            })
        except Exception:
            pass
    else:
        console.print(f"[red]Render failed:[/]\n{res['error']}")
    return res


def suggest_cuts(tl_path: Path, silence: bool = True, fillers: bool = True,
                 maybe: bool = False, min_silence: float = 0.6, threshold: float = -35.0) -> list[dict]:
    tl = T.load(tl_path)
    out = []
    if silence:
        with console.status("[bold]Detecting silences…"):
            out += A.detect_silences(Path(tl["source"]), min_silence, threshold)
    if fillers:
        data = ensure_transcript(tl_path)
        if data:
            out += A.find_fillers(data, include_maybe=maybe)
    out.sort(key=lambda c: c["start"])
    return out


def print_cuts(cuts: list[dict], title: str = "Cuts") -> None:
    table = Table(title=title, box=box.SIMPLE_HEAD)
    table.add_column("start", justify="right")
    table.add_column("end", justify="right")
    table.add_column("len", justify="right")
    table.add_column("reason")
    for c in cuts:
        table.add_row(f"{c['start']:.2f}", f"{c['end']:.2f}", f"{c['end'] - c['start']:.2f}s",
                      c.get("reason", "") + (" [dim](maybe)[/]" if c.get("confidence") == "maybe" else ""))
    console.print(table)


# --------------------------------------------------------------------------
# click commands
# --------------------------------------------------------------------------

class EditGroup(click.Group):
    """``ncaster edit <video|timeline>`` opens the editor directly."""

    def parse_args(self, ctx, args):
        if args and args[0] not in self.commands and not args[0].startswith("-"):
            args = ["open"] + args
        return super().parse_args(ctx, args)


@click.group("edit", cls=EditGroup)
def edit():
    """Edit videos: cuts, 9:16 reframe, burned captions, overlays — previewed in the terminal.

    \b
    The edit lives in <video>.ncedit.json, shared with Claude Code via `ncaster mcp`.
      ncaster edit talk.mp4              open the terminal editor
      ncaster edit new talk.mp4          create the timeline
      ncaster edit preview talk.mp4 -p 2 watch part 2 in the terminal
      ncaster edit render talk.mp4       render the final video
    """


@edit.command("new")
@click.argument("source", type=click.Path(exists=True, dir_okay=False))
@click.option("-a", "--aspect", default="9:16", type=click.Choice(list(T.ASPECTS)), show_default=True)
@click.option("--reframe", default="crop", type=click.Choice(T.REFRAME_MODES), show_default=True,
              help="crop = fill the frame; blur = fit with blurred background.")
@click.option("--subtitles/--no-subtitles", default=True, show_default=True)
@click.option("--transcribe/--no-transcribe", default=True, show_default=True,
              help="Transcribe with word timestamps now (needed for captions and filler cuts).")
@click.option("-l", "--language", default="auto", type=click.Choice(["auto", "en", "pt"]))
@click.option("-m", "--model", "model_size", default="small", show_default=True)
@click.option("--force", is_flag=True, help="Overwrite an existing timeline.")
def new_cmd(source, aspect, reframe, subtitles, transcribe, language, model_size, force):
    """Create a timeline for SOURCE."""
    src = Path(source).resolve()
    tl_path = T.default_path(src)
    if tl_path.is_file() and not force:
        raise click.ClickException(f"{tl_path.name} already exists (use --force to overwrite)")
    tl = T.new_timeline(src, aspect=aspect, reframe=reframe)
    tl["subtitles"]["enabled"] = subtitles
    T.save(tl_path, tl, record_history=False)
    console.print(f"[green]Created[/] {tl_path}")
    if transcribe:
        ensure_transcript(tl_path, language, model_size)
    print_overview(tl_path)


@edit.command("show")
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
def show_cmd(target):
    """Show the timeline and its preview parts."""
    tl_path = resolve_timeline(target)
    print_overview(tl_path)
    tl = T.load(tl_path)
    if tl["cuts"]:
        print_cuts(tl["cuts"])


@edit.command("suggest")
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
@click.option("--silence/--no-silence", default=True, show_default=True)
@click.option("--fillers/--no-fillers", default=True, show_default=True)
@click.option("--maybe", is_flag=True, help="Also flag ambiguous fillers (tipo, né, like…).")
@click.option("--min-silence", default=0.6, show_default=True, help="Seconds.")
@click.option("--threshold", default=-35.0, show_default=True, help="Silence threshold in dB.")
@click.option("--apply", is_flag=True, help="Add the suggested cuts to the timeline.")
def suggest_cmd(target, silence, fillers, maybe, min_silence, threshold, apply):
    """Find silences and filler words to cut."""
    tl_path = resolve_timeline(target)
    cuts = suggest_cuts(tl_path, silence, fillers, maybe, min_silence, threshold)
    if not cuts:
        console.print("[dim]Nothing to cut.[/]")
        return
    print_cuts(cuts, f"Suggested cuts ({sum(c['end'] - c['start'] for c in cuts):.1f}s)")
    if apply:
        T.save(tl_path, T.add_cuts(T.load(tl_path), cuts))
        console.print(f"[green]Applied {len(cuts)} cuts.[/]  [dim]ncaster edit undo to revert[/]")


@edit.command("cut")
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
@click.argument("start", type=float)
@click.argument("end", type=float)
@click.option("-r", "--reason", default="manual")
def cut_cmd(target, start, end, reason):
    """Cut source range START–END (seconds)."""
    tl_path = resolve_timeline(target)
    T.save(tl_path, T.add_cuts(T.load(tl_path), [{"start": start, "end": end, "reason": reason}]))
    print_overview(tl_path)


@edit.command("uncut")
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
@click.argument("start", type=float)
@click.argument("end", type=float)
def uncut_cmd(target, start, end):
    """Restore source range START–END (seconds)."""
    tl_path = resolve_timeline(target)
    T.save(tl_path, T.remove_cuts(T.load(tl_path), start, end))
    print_overview(tl_path)


@edit.command("undo")
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
def undo_cmd(target):
    """Undo the last change to the timeline."""
    tl_path = resolve_timeline(target)
    try:
        T.undo(tl_path)
    except T.TimelineError as e:
        raise click.ClickException(str(e))
    console.print("[green]Undone.[/]")
    print_overview(tl_path)


@edit.command("preview")
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
@click.option("-p", "--part", "part_nums", type=int, multiple=True, help="Part number (repeatable).")
@click.option("--all", "all_parts", is_flag=True, help="Play every part in sequence.")
@click.option("--start", type=float, default=None, help="Output-time window start (s).")
@click.option("--end", type=float, default=None, help="Output-time window end (s).")
@click.option("--no-play", is_flag=True, help="Only render; print the file path.")
@click.option("--loop", is_flag=True, help="Loop the preview until you press q.")
def preview_cmd(target, part_nums, all_parts, start, end, no_play, loop):
    """Render and play preview parts in the terminal."""
    tl_path = resolve_timeline(target)
    tl = T.load(tl_path)
    if start is not None or end is not None:
        a, b = start or 0.0, end if end is not None else T.out_duration(tl)
        preview_window(tl, a, b, f"w{a:.0f}-{b:.0f}", not no_play, loop)
        return
    windows = T.parts(tl)
    nums = range(1, len(windows) + 1) if all_parts else (part_nums or (1,))
    for n in nums:
        preview_part(tl_path, n, play=not no_play, loop=loop)


@edit.command("play")
@click.argument("video", type=click.Path(exists=True, dir_okay=False))
@click.option("--loop", is_flag=True)
def play_cmd(video, loop):
    """Play any video in the terminal (mpv)."""
    if not P.play_in_terminal(Path(video), loop=loop):
        raise click.ClickException("mpv is not installed")


@edit.command("render")
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--output", default=None, help="Output file (default: <stem>_edit.mp4).")
@click.option("-q", "--quality", type=click.Choice(["high", "medium", "low"]), default=None)
def render_cmd(target, output, quality):
    """Render the final edited video."""
    if not render_final(resolve_timeline(target), output, quality)["ok"]:
        raise SystemExit(1)


@edit.command("open")
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
def open_cmd(target):
    """Interactive editor: browse parts, preview, cut, render."""
    editor_loop(resolve_timeline(target, create=True))


def editor_loop(tl_path: Path) -> None:
    while True:
        windows = print_overview(tl_path)  # reloads: picks up edits made by Claude Code
        tl = T.load(tl_path)
        choices = [
            questionary.Choice("▶  Preview a part", value="part"),
            questionary.Choice("▶▶ Preview all parts", value="all"),
            questionary.Choice("✂  Suggest cuts (silences / fillers)", value="suggest"),
            questionary.Choice("⬚  Reframe", value="reframe"),
            questionary.Choice(f"💬 Captions ({tl['subtitles']['style'] if tl['subtitles']['enabled'] else 'off'})",
                               value="subs"),
            questionary.Choice("↶  Undo last change", value="undo"),
            questionary.Choice("⟳  Reload", value="reload"),
            questionary.Choice("🎬 Render final video", value="render"),
            questionary.Choice("Quit", value="quit"),
        ]
        action = questionary.select("Editor:", choices=choices, style=Q_STYLE).ask()
        if action in (None, "quit"):
            console.print(f"[dim]Timeline saved at {tl_path}[/]")
            return
        if action == "part":
            pick = questionary.select(
                "Part:", style=Q_STYLE,
                choices=[questionary.Choice(f"{i:>2}  {human_duration(a)}–{human_duration(b)}", value=i)
                         for i, (a, b) in enumerate(windows, 1)],
            ).ask()
            if pick:
                preview_part(tl_path, pick)
        elif action == "all":
            for i in range(1, len(windows) + 1):
                preview_part(tl_path, i)
        elif action == "suggest":
            cuts = suggest_cuts(tl_path)
            if not cuts:
                console.print("[dim]Nothing to cut.[/]")
                continue
            print_cuts(cuts, f"Suggested cuts ({sum(c['end'] - c['start'] for c in cuts):.1f}s)")
            if questionary.confirm(f"Apply {len(cuts)} cuts?", default=True, style=Q_STYLE).ask():
                T.save(tl_path, T.add_cuts(T.load(tl_path), cuts))
        elif action == "reframe":
            aspect = questionary.select("Aspect:", choices=list(T.ASPECTS), style=Q_STYLE,
                                        default=tl["reframe"]["aspect"]).ask()
            if not aspect:
                continue
            mode = questionary.select("Mode:", style=Q_STYLE, choices=[
                questionary.Choice("crop — fill the frame", value="crop"),
                questionary.Choice("blur — fit, blurred background", value="blur"),
            ]).ask() if aspect != "source" else "crop"
            if mode:
                tl["reframe"].update(aspect=aspect, mode=mode)
                T.save(tl_path, tl)
        elif action == "subs":
            style = questionary.select("Captions:", style=Q_STYLE, choices=[
                questionary.Choice("words — few words, current one highlighted", value="words"),
                questionary.Choice("phrase — one line per phrase", value="phrase"),
                questionary.Choice("off", value="off"),
            ]).ask()
            if style:
                if style != "off" and not ensure_transcript(tl_path):
                    continue
                tl = T.load(tl_path)
                tl["subtitles"]["enabled"] = style != "off"
                if style != "off":
                    tl["subtitles"]["style"] = style
                T.save(tl_path, tl)
        elif action == "undo":
            try:
                T.undo(tl_path)
            except T.TimelineError as e:
                console.print(f"[yellow]{e}[/]")
        elif action == "render":
            render_final(tl_path)
