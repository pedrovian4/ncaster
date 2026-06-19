"""Interactive mode: scan a directory, fuzzy-pick files (fzf), then guide the
user through conversion or transcription."""

import shutil
import subprocess
from pathlib import Path

import questionary
from questionary import Style as QStyle
from rich import box
from rich.panel import Panel
from rich.table import Table

from .config import FORMATS, MEDIA_EXTENSIONS, TRANSCRIBE_LANGS
from .console import console
from .convert import run_conversion
from .probe import human_duration, human_size, probe_duration, probe_format_name
from .transcribe import faster_whisper_available, run_transcription, warn_missing_whisper

# questionary style that matches Rich's cyan theme.
Q_STYLE = QStyle([
    ("qmark",       "fg:#00d7ff bold"),
    ("question",    "bold"),
    ("answer",      "fg:#00d7ff bold"),
    ("pointer",     "fg:#00d7ff bold"),
    ("highlighted", "fg:#00d7ff bold"),
    ("selected",    "fg:#afffff"),
    ("separator",   "fg:#555555"),
    ("instruction", "fg:#555555"),
    ("text",        ""),
    ("disabled",    "fg:#555555 italic"),
])


def scan_media_files(directory: Path, recursive: bool = False) -> list[Path]:
    it = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        p for p in it
        if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS
    )


def _file_label(f: Path, base: Path) -> str:
    """Tab-separated 'relpath<TAB>metadata' line for fzf / display."""
    try:
        name = str(f.relative_to(base))
    except ValueError:
        name = f.name
    size = human_size(f.stat().st_size)
    fmt = probe_format_name(f)
    dur = human_duration(probe_duration(f))
    return f"{name}\t{fmt:<6} {size:>9}  {dur}"


def select_files_fzf(files: list[Path], base: Path) -> list[Path] | None:
    """Fuzzy multi-select with fzf. Returns selected paths, or None if cancelled."""
    by_name: dict[str, Path] = {}
    lines = []
    for f in files:
        line = _file_label(f, base)
        key = line.split("\t", 1)[0]
        by_name[key] = f
        lines.append(line)

    proc = subprocess.run(
        [
            "fzf", "--multi",
            "--delimiter=\t", "--with-nth=1,2", "--nth=1",
            "--height=~70%", "--reverse", "--border", "--cycle",
            "--prompt=cast> ",
            "--marker=▶ ", "--pointer=→",
            "--header=TAB/Shift-TAB: select  ·  ENTER: confirm  ·  ESC: cancel  ·  type to filter",
        ],
        input="\n".join(lines),
        capture_output=True, text=True,
    )

    # 0 = selection made, 1 = no match, 130 = cancelled (ESC/Ctrl-C)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None

    selected = []
    for line in proc.stdout.strip().split("\n"):
        key = line.split("\t", 1)[0]
        if key in by_name:
            selected.append(by_name[key])
    return selected


def select_files_questionary(files: list[Path], base: Path) -> list[Path] | None:
    choices = []
    for f in files:
        name, meta = _file_label(f, base).split("\t", 1)
        choices.append(questionary.Choice(title=f"{name:<40} {meta}", value=f))
    return questionary.checkbox(
        "Select files:", choices=choices, style=Q_STYLE,
    ).ask()


def interactive_mode(directory: Path | None = None, recursive: bool = False) -> None:
    base = (directory or Path.cwd()).expanduser().resolve()

    use_fzf = shutil.which("fzf") is not None
    console.print(Panel.fit(
        f"[bold cyan]ncaster[/]  [dim]{base}[/]"
        + ("  [dim](recursive)[/]" if recursive else "")
        + ("  [dim]· fzf[/]" if use_fzf else ""),
        border_style="cyan",
    ))

    files = scan_media_files(base, recursive=recursive)
    if not files:
        where = "directory tree" if recursive else "directory"
        console.print(f"[yellow]No media files found in the {where}.[/]")
        return

    selected = (select_files_fzf if use_fzf else select_files_questionary)(files, base)
    if not selected:
        console.print("[dim]No files selected. Bye.[/]")
        return

    action = questionary.select(
        "What do you want to do?",
        choices=[
            questionary.Choice("Convert format", value="convert"),
            questionary.Choice("Transcribe speech → text/subtitles (Whisper)",
                               value="transcribe"),
        ],
        style=Q_STYLE,
    ).ask()

    if action == "convert":
        _interactive_convert(selected, base)
    elif action == "transcribe":
        _interactive_transcribe(selected, base)


def _ask_output_dir(base: Path) -> Path | None:
    out_dir_str = questionary.text(
        "Output directory:", default=str(base), style=Q_STYLE,
    ).ask()
    if out_dir_str is None:
        return None
    out_dir = Path(out_dir_str).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _interactive_convert(selected: list[Path], base: Path) -> None:
    fmt = questionary.select(
        "Target format:", choices=sorted(FORMATS.keys()), style=Q_STYLE,
    ).ask()
    if not fmt:
        return

    quality = questionary.select(
        "Quality:",
        choices=[
            questionary.Choice("lossless — identical quality, largest file", value="lossless"),
            questionary.Choice("high     — excellent quality  (recommended)", value="high"),
            questionary.Choice("medium   — good quality, smaller file",       value="medium"),
            questionary.Choice("low      — smallest file, noticeable loss",   value="low"),
        ],
        default="high", style=Q_STYLE,
    ).ask()
    if not quality:
        return

    speed = questionary.select(
        "Encoding speed:",
        choices=[
            questionary.Choice("veryslow — best compression, slowest",    value="veryslow"),
            questionary.Choice("slow     — great compression  (default)", value="slow"),
            questionary.Choice("medium   — balanced",                     value="medium"),
            questionary.Choice("fast     — quick, larger output",         value="fast"),
        ],
        default="slow", style=Q_STYLE,
    ).ask()
    if not speed:
        return

    out_dir = _ask_output_dir(base)
    if out_dir is None:
        return

    out_ext = FORMATS[fmt]["ext"]

    console.print()
    table = Table(box=box.SIMPLE_HEAD, show_header=True)
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Size", justify="right")
    table.add_column("→ Output", style="green", no_wrap=True)
    for f in selected:
        table.add_row(f.name, human_size(f.stat().st_size), f"{f.stem}.{out_ext}")
    console.print(table)

    confirmed = questionary.confirm(
        f"Convert {len(selected)} file(s) → {fmt.upper()}  [{quality} / {speed}]?",
        default=True, style=Q_STYLE,
    ).ask()
    if not confirmed:
        console.print("[dim]Cancelled.[/]")
        return

    console.print()
    results = []
    for src in selected:
        dst = out_dir / f"{src.stem}.{out_ext}"
        if dst.resolve() == src.resolve():
            dst = out_dir / f"{src.stem}_converted.{out_ext}"

        console.print(f"[bold]{src.name}[/] → [green]{dst.name}[/]")
        ok = run_conversion(src, dst, fmt, quality, speed, [], False, False)

        if ok:
            src_mb = src.stat().st_size / 1_048_576
            dst_mb = dst.stat().st_size / 1_048_576
            ratio = dst_mb / src_mb * 100 if src_mb else 0
            console.print(
                f"  [green]Done[/]  {src_mb:.1f} MB → {dst_mb:.1f} MB  "
                f"([{'green' if ratio <= 100 else 'yellow'}]{ratio:.0f}%[/])"
            )
        else:
            console.print("  [red]Failed[/]")
        results.append(ok)

    _print_summary(results, "converted")


def _interactive_transcribe(selected: list[Path], base: Path) -> None:
    if not faster_whisper_available():
        warn_missing_whisper()
        return

    language = questionary.select(
        "Spoken language:",
        choices=[questionary.Choice(f"{code:<5} {name}", value=code)
                 for code, name in TRANSCRIBE_LANGS.items()],
        default="auto", style=Q_STYLE,
    ).ask()
    if not language:
        return

    model_size = questionary.select(
        "Whisper model (bigger = more accurate, slower):",
        choices=[
            questionary.Choice("tiny     — fastest, lowest accuracy", value="tiny"),
            questionary.Choice("base     — fast",                     value="base"),
            questionary.Choice("small    — balanced  (recommended)",  value="small"),
            questionary.Choice("medium   — accurate, slower",         value="medium"),
            questionary.Choice("large-v3 — best accuracy, slowest",   value="large-v3"),
        ],
        default="small", style=Q_STYLE,
    ).ask()
    if not model_size:
        return

    out_fmt = questionary.select(
        "Output format:",
        choices=[
            questionary.Choice("srt  — subtitles with timestamps", value="srt"),
            questionary.Choice("vtt  — WebVTT subtitles",          value="vtt"),
            questionary.Choice("txt  — plain text",                value="txt"),
            questionary.Choice("json — segments + metadata",       value="json"),
        ],
        default="srt", style=Q_STYLE,
    ).ask()
    if not out_fmt:
        return

    out_dir = _ask_output_dir(base)
    if out_dir is None:
        return

    console.print()
    table = Table(box=box.SIMPLE_HEAD, show_header=True)
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("→ Output", style="green", no_wrap=True)
    for f in selected:
        table.add_row(f.name, f"{f.stem}.{out_fmt}")
    console.print(table)

    lang_label = TRANSCRIBE_LANGS.get(language, language)
    confirmed = questionary.confirm(
        f"Transcribe {len(selected)} file(s)  [{lang_label} · {model_size} · {out_fmt}]?",
        default=True, style=Q_STYLE,
    ).ask()
    if not confirmed:
        console.print("[dim]Cancelled.[/]")
        return

    console.print("[dim]Loading Whisper model (first run downloads it)…[/]\n")
    results = []
    for src in selected:
        dst = out_dir / f"{src.stem}.{out_fmt}"
        console.print(f"[bold]{src.name}[/] → [green]{dst.name}[/]")
        ok = run_transcription(src, dst, model_size, language, out_fmt)
        if not ok:
            console.print("  [red]Failed[/]")
        results.append(ok)

    _print_summary(results, "transcribed")


def _print_summary(results: list[bool], verb: str) -> None:
    done = sum(1 for ok in results if ok)
    failed = len(results) - done
    console.print()
    if failed:
        console.print(f"[bold]Done:[/] {done} {verb}, [red]{failed} failed[/].")
    else:
        console.print(f"[bold green]All {done} file(s) {verb} successfully.[/]")
