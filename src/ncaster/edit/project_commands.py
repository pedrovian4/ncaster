"""``ncaster project …`` — centralized editing projects."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click
import questionary
from rich import box
from rich.table import Table

from ..console import console
from ..probe import human_duration, human_size
from . import projects as PR
from . import timeline as T
from .commands import Q_STYLE, editor_loop, ensure_transcript


def _ago(iso: str | None) -> str:
    if not iso:
        return "?"
    delta = datetime.now() - datetime.fromisoformat(iso)
    if delta.days >= 1:
        return f"{delta.days}d ago"
    hours = delta.seconds // 3600
    return f"{hours}h ago" if hours else f"{max(1, delta.seconds // 60)}m ago"


def print_projects() -> list[dict]:
    root = PR.projects_root()
    infos = sorted((PR.info(p) for p in PR.all_projects()), key=lambda i: i["updated"], reverse=True)
    if not infos:
        console.print(f"[dim]No projects yet in {root}[/]\n"
                      "Create one: [cyan]ncaster project new \"Meu vídeo\" ~/Downloads/take1.mp4[/]")
        return []
    table = Table(title=f"Projects  [dim]{root}[/]", box=box.SIMPLE_HEAD, title_justify="left")
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("project", style="bold")
    table.add_column("videos", justify="right")
    table.add_column("edited", justify="right")
    table.add_column("renders", justify="right")
    table.add_column("b-roll", justify="right")
    table.add_column("size", justify="right")
    table.add_column("updated", style="dim")
    for i, inf in enumerate(infos, 1):
        edited = sum(1 for v in inf["videos"] if v.get("timeline"))
        table.add_row(str(i), f"{inf['name']} [dim]{inf['slug']}[/]" if inf["name"] != inf["slug"] else inf["name"],
                      str(len(inf["videos"])), str(edited), str(len(inf["renders"])),
                      str(len(inf["broll"])), human_size(inf["size_bytes"]), _ago(inf["updated"]))
    console.print(table)
    return infos


def print_project(project: Path) -> dict:
    inf = PR.info(project)
    console.print(f"[bold cyan]{inf['name']}[/]  [dim]{inf['path']}[/]")
    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("video")
    table.add_column("length", justify="right")
    table.add_column("edit")
    for v in inf["videos"]:
        name = Path(v["video"]).name
        if v.get("timeline"):
            edit = (f"{human_duration(v['output_duration'])} · {v['cuts']} cuts · {v['aspect']}"
                    if "output_duration" in v else f"[red]{v.get('error')}[/]")
            table.add_row(name, human_duration(v.get("duration")), edit)
        else:
            table.add_row(name, "", "[dim]not started[/]")
    console.print(table)
    if inf["broll"]:
        console.print(f"[dim]b-roll:[/] {', '.join(inf['broll'][:12])}"
                      + (f" [dim]+{len(inf['broll']) - 12}[/]" if len(inf["broll"]) > 12 else ""))
    for r in inf["renders"][:5]:
        console.print(f"[green]render:[/] {Path(r).name}")
    return inf


def _report_moves(moved: list[tuple[Path, Path]]) -> None:
    for old, new in moved:
        if old != new:
            console.print(f"  [yellow]moved[/] {old} → [green]{new}[/]")


def pick_project(allow_new: bool = False, allow_none_label: str | None = None):
    """Ask for a project. Returns a Path, "new", None (keep/cancel)."""
    choices = [questionary.Choice(f"{PR.read_meta(p).get('name', p.name)}", value=p) for p in
               sorted(PR.all_projects(), key=lambda p: p.stat().st_mtime, reverse=True)]
    if allow_new:
        choices.insert(0, questionary.Choice("+ New project…", value="new"))
    if allow_none_label:
        choices.append(questionary.Choice(allow_none_label, value="none"))
    if not choices:
        return None
    return questionary.select("Project:", choices=choices, style=Q_STYLE).ask()


def ask_new_project() -> Path | None:
    name = questionary.text("Project name:", style=Q_STYLE).ask()
    if not name or not name.strip():
        return None
    try:
        path = PR.create_project(name)
    except PR.ProjectError as e:
        console.print(f"[red]{e}[/]")
        return None
    console.print(f"[green]Created[/] {path}")
    return path


def open_project(project: Path) -> None:
    while True:
        inf = print_project(project)
        choices = [questionary.Choice(
            f"{Path(v['video']).name}" + ("" if v.get("timeline") else "  (new edit)"), value=v["video"])
            for v in inf["videos"]]
        choices += [questionary.Choice("+ Add videos (moves them here)…", value="add"),
                    questionary.Choice("Back", value="back")]
        pick = questionary.select("Open:", choices=choices, style=Q_STYLE).ask()
        if pick in (None, "back"):
            return
        if pick == "add":
            raw = questionary.path("Video file:", style=Q_STYLE).ask()
            if raw:
                try:
                    _report_moves(PR.add_media(project, [Path(raw)]))
                except PR.ProjectError as e:
                    console.print(f"[red]{e}[/]")
            continue
        start_edit(Path(pick))


def start_edit(video: Path) -> None:
    """Open the editor for a video, creating its timeline first if needed."""
    tl_path = T.default_path(video)
    if not tl_path.is_file():
        aspect = questionary.select(
            f"Output aspect for {video.name}:", style=Q_STYLE,
            choices=[questionary.Choice("9:16 — Shorts / Reels / TikTok", value="9:16"),
                     questionary.Choice("source — keep original", value="source"),
                     questionary.Choice("1:1 — square", value="1:1"),
                     questionary.Choice("4:5 — feed", value="4:5")],
        ).ask()
        if not aspect:
            return
        try:
            T.save(tl_path, T.new_timeline(video, aspect=aspect), record_history=False)
        except T.TimelineError as e:
            console.print(f"[red]{e}[/]")
            return
        if questionary.confirm("Transcribe now (captions + filler cuts)?", default=True, style=Q_STYLE).ask():
            if ensure_transcript(tl_path):
                tl = T.load(tl_path)
                tl["subtitles"]["enabled"] = True
                T.save(tl_path, tl)
    editor_loop(tl_path)


def _resolve(ref: str) -> Path:
    try:
        return PR.find_project(ref)
    except PR.ProjectError as e:
        raise click.ClickException(str(e))


@click.group("project", invoke_without_command=True)
@click.pass_context
def project(ctx):
    """Centralized editing projects (default root: ~/Videos/ncaster).

    \b
    Each project keeps its videos (moved in), b-roll, timelines and renders:
      <root>/<project>/media/    <root>/<project>/broll/    <root>/<project>/renders/
    \b
      ncaster project                       list projects
      ncaster project new "Curso" a.mp4     create + move videos in
      ncaster project open curso            pick a video and edit it
      cd "$(ncaster project path curso)"
    """
    if ctx.invoked_subcommand is None:
        print_projects()


@project.command("list")
def list_cmd():
    """List all projects."""
    print_projects()


@project.command("new")
@click.argument("name")
@click.argument("videos", nargs=-1, type=click.Path(exists=True, dir_okay=False))
def new_cmd(name, videos):
    """Create project NAME and move VIDEOS into it."""
    try:
        path = PR.create_project(name)
        console.print(f"[green]Created[/] {path}")
        _report_moves(PR.add_media(path, [Path(v) for v in videos]))
    except PR.ProjectError as e:
        raise click.ClickException(str(e))
    print_project(path)


@project.command("add")
@click.argument("name")
@click.argument("videos", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
def add_cmd(name, videos):
    """Move VIDEOS into project NAME (timelines/transcripts come along)."""
    proj = _resolve(name)
    try:
        _report_moves(PR.add_media(proj, [Path(v) for v in videos]))
    except PR.ProjectError as e:
        raise click.ClickException(str(e))
    print_project(proj)


@project.command("show")
@click.argument("name")
def show_cmd(name):
    """Show a project's videos, edits, b-roll and renders."""
    print_project(_resolve(name))


@project.command("open")
@click.argument("name", required=False)
def open_cmd(name):
    """Pick a video in a project and open the editor."""
    proj = _resolve(name) if name else pick_project(allow_new=True)
    if proj == "new":
        proj = ask_new_project()
    if proj:
        open_project(proj)


@project.command("path")
@click.argument("name", required=False)
def path_cmd(name):
    """Print a project's folder (or the projects root)."""
    click.echo(_resolve(name) if name else PR.projects_root())


@project.command("root")
@click.argument("directory", required=False, type=click.Path(file_okay=False))
def root_cmd(directory):
    """Show or set the folder where all projects live."""
    if directory:
        from ..settings import save_setting
        old = PR.all_projects()
        target = Path(directory).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        cfg = save_setting(PR.ROOT_ENV, str(target))
        console.print(f"[green]Projects root[/] → {target}  [dim](saved in {cfg})[/]")
        if old and old[0].parent != target:
            console.print(f"[yellow]{len(old)} existing project(s) stay in {old[0].parent}.[/] "
                          "Move the folders by hand if you want them here.")
    else:
        click.echo(PR.projects_root())


# `ncaster edit` integration: offer to move a loose video into a project.
def maybe_move_into_project(video: Path) -> Path | None:
    """For a video outside any project, offer to move it into one. Returns the
    (possibly new) video path, or None if cancelled."""
    if T.find_project_root(video):
        return video
    pick = pick_project(allow_new=True, allow_none_label=f"Keep it where it is ({video.parent})")
    if pick is None:
        return None
    if pick == "none":
        return video
    if pick == "new":
        pick = ask_new_project()
        if pick is None:
            return None
    moved = PR.add_media(pick, [video])
    _report_moves(moved)
    return moved[0][1]

