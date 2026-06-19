#!/usr/bin/env python3
"""ncaster — high-quality video/audio format converter."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import click
import questionary
from questionary import Style as QStyle
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()

# ---------------------------------------------------------------------------
# Media extensions to scan for
# ---------------------------------------------------------------------------

MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv",
    ".ts", ".mts", ".m2ts", ".3gp", ".ogv", ".vob", ".mpg", ".mpeg",
    ".mp3", ".aac", ".flac", ".wav", ".opus", ".ogg", ".m4a", ".wma", ".aiff",
}

# ---------------------------------------------------------------------------
# Format / codec profiles
# ---------------------------------------------------------------------------

FORMATS = {
    "mp4":  {"vcodec": "libx264",    "acodec": "aac",        "ext": "mp4",  "audio_flags": []},
    "mov":  {"vcodec": "libx264",    "acodec": "aac",        "ext": "mov",  "audio_flags": []},
    "mkv":  {"vcodec": "libx265",    "acodec": "libopus",    "ext": "mkv",  "audio_flags": ["-ac", "2", "-ar", "48000"]},
    "webm": {"vcodec": "libvpx-vp9", "acodec": "libopus",    "ext": "webm", "audio_flags": ["-ac", "2", "-ar", "48000"]},
    "avi":  {"vcodec": "mpeg4",      "acodec": "mp3",        "ext": "avi",  "audio_flags": []},
    "gif":  {"vcodec": "gif",        "acodec": None,         "ext": "gif",  "audio_flags": []},
    "mp3":  {"vcodec": None,         "acodec": "libmp3lame", "ext": "mp3",  "audio_flags": []},
    "aac":  {"vcodec": None,         "acodec": "aac",        "ext": "aac",  "audio_flags": []},
    "flac": {"vcodec": None,         "acodec": "flac",       "ext": "flac", "audio_flags": []},
    "wav":  {"vcodec": None,         "acodec": "pcm_s16le",  "ext": "wav",  "audio_flags": []},
    "opus": {"vcodec": None,         "acodec": "libopus",    "ext": "opus", "audio_flags": ["-ac", "2", "-ar", "48000"]},
}

QUALITY_CRF = {
    "libx264":    {"lossless": 0,  "high": 18, "medium": 23, "low": 28},
    "libx265":    {"lossless": 0,  "high": 20, "medium": 26, "low": 32},
    "libvpx-vp9": {"lossless": 0,  "high": 25, "medium": 33, "low": 42},
    "mpeg4":      {"lossless": 1,  "high": 2,  "medium": 5,  "low": 10},
}

AUDIO_BITRATE = {"high": "320k", "medium": "192k", "low": "128k"}

PRESETS = {
    "libx264": ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"],
    "libx265": ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"],
}

# questionary style that matches rich's cyan theme
Q_STYLE = QStyle([
    ("qmark",        "fg:#00d7ff bold"),
    ("question",     "bold"),
    ("answer",       "fg:#00d7ff bold"),
    ("pointer",      "fg:#00d7ff bold"),
    ("highlighted",  "fg:#00d7ff bold"),
    ("selected",     "fg:#afffff"),
    ("separator",    "fg:#555555"),
    ("instruction",  "fg:#555555"),
    ("text",         ""),
    ("disabled",     "fg:#555555 italic"),
])

# ---------------------------------------------------------------------------
# FFprobe helpers
# ---------------------------------------------------------------------------

def probe_duration(path: Path) -> float | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def probe_format_name(path: Path) -> str:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip().split(",")[0].upper()
    except Exception:
        return path.suffix.lstrip(".").upper()


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(secs: float | None) -> str:
    if secs is None:
        return "?"
    secs = int(secs)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# FFmpeg conversion
# ---------------------------------------------------------------------------

def build_ffmpeg_cmd(
    src: Path, dst: Path, fmt: str, quality: str, speed: str,
    extra_flags: list[str], gpu: bool,
) -> list[str]:
    profile = FORMATS[fmt]
    vcodec = profile["vcodec"]
    acodec = profile["acodec"]

    cmd = ["ffmpeg", "-y", "-i", str(src)]

    if vcodec:
        vcodec_actual = {"libx264": "h264_nvenc", "libx265": "hevc_nvenc"}.get(vcodec, vcodec) if gpu else vcodec
        cmd += ["-c:v", vcodec_actual]

        if fmt == "gif":
            cmd += ["-vf", "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse", "-loop", "0"]
        else:
            crf_table = QUALITY_CRF.get(vcodec)
            if crf_table:
                crf = crf_table.get(quality, crf_table["medium"])
                if vcodec == "libvpx-vp9":
                    cmd += ["-crf", str(crf), "-b:v", "0"]
                else:
                    cmd += ["-crf", str(crf)]
            preset_list = PRESETS.get(vcodec)
            if preset_list and speed in preset_list and not gpu:
                cmd += ["-preset", speed]
    else:
        cmd += ["-vn"]

    if acodec:
        cmd += ["-c:a", acodec]
        cmd += profile.get("audio_flags", [])
        if acodec not in ("flac", "pcm_s16le"):
            cmd += ["-b:a", AUDIO_BITRATE.get(quality, "192k")]
    else:
        cmd += ["-an"]

    cmd += extra_flags
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def parse_progress_us(line: str) -> float | None:
    m = re.match(r"out_time_us=(\d+)", line)
    return int(m.group(1)) / 1_000_000 if m else None


def run_conversion(
    src: Path, dst: Path, fmt: str, quality: str, speed: str,
    extra_flags: list[str], gpu: bool, dry_run: bool,
) -> bool:
    cmd = build_ffmpeg_cmd(src, dst, fmt, quality, speed, extra_flags, gpu)

    if dry_run:
        console.print(f"  [dim]$ {' '.join(cmd)}[/]")
        return True

    duration = probe_duration(src)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=30),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"{src.name} → {dst.name}", total=duration or 100)

        with tempfile.TemporaryFile(mode="w+") as stderr_tmp:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_tmp, text=True)

            for line in proc.stdout:
                secs = parse_progress_us(line.strip())
                if secs is not None and duration:
                    progress.update(task, completed=min(secs, duration))
                elif line.strip() == "progress=end":
                    progress.update(task, completed=duration or 100)

            proc.wait()
            stderr_tmp.seek(0)
            stderr_text = stderr_tmp.read()

    if proc.returncode != 0:
        console.print(f"[red]  Error:[/] {stderr_text[-600:] if stderr_text else 'ffmpeg failed'}")
        return False
    return True


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

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
    by_name = {}
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
        "Select files to convert:",
        choices=choices,
        style=Q_STYLE,
    ).ask()


def interactive_mode(directory: Path | None = None, recursive: bool = False):
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

    if use_fzf:
        selected = select_files_fzf(files, base)
    else:
        selected = select_files_questionary(files, base)

    if not selected:
        console.print("[dim]No files selected. Bye.[/]")
        return

    fmt = questionary.select(
        "Target format:",
        choices=sorted(FORMATS.keys()),
        style=Q_STYLE,
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
        default="high",
        style=Q_STYLE,
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
        default="slow",
        style=Q_STYLE,
    ).ask()

    if not speed:
        return

    out_dir_str = questionary.text(
        "Output directory:",
        default=str(base),
        style=Q_STYLE,
    ).ask()

    if out_dir_str is None:
        return

    out_dir = Path(out_dir_str).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    profile = FORMATS[fmt]
    out_ext = profile["ext"]

    # Summary before running
    console.print()
    table = Table(box=box.SIMPLE_HEAD, show_header=True)
    table.add_column("File",   style="cyan", no_wrap=True)
    table.add_column("Size",   justify="right")
    table.add_column("→ Output", style="green", no_wrap=True)
    for f in selected:
        dst_name = f"{f.stem}.{out_ext}"
        table.add_row(f.name, human_size(f.stat().st_size), dst_name)
    console.print(table)

    confirmed = questionary.confirm(
        f"Convert {len(selected)} file(s) → {fmt.upper()}  [{quality} / {speed}]?",
        default=True,
        style=Q_STYLE,
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
        results.append((src, dst, ok))

    done = sum(1 for _, _, ok in results if ok)
    failed = len(results) - done
    console.print()
    if failed:
        console.print(f"[bold]Done:[/] {done} converted, [red]{failed} failed[/].")
    else:
        console.print(f"[bold green]All {done} file(s) converted successfully.[/]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class NcasterGroup(click.Group):
    """Group that treats a leading path (e.g. '.', './videos') as interactive mode."""

    def parse_args(self, ctx, args):
        non_opts = [a for a in args if not a.startswith("-")]
        if non_opts:
            first = non_opts[0]
            if first not in self.commands and (first == "." or Path(first).exists()):
                args = ["interactive"] + args
        return super().parse_args(ctx, args)


@click.group(cls=NcasterGroup, invoke_without_command=True)
@click.version_option("1.0.0", prog_name="ncaster")
@click.pass_context
def cli(ctx):
    """ncaster — cast video and audio between formats with high quality.

    \b
    Interactive mode (fuzzy-pick files, then choose format/quality):
      ncaster                 scan the current directory
      ncaster .               same as above
      ncaster ~/Videos        scan another directory
      ncaster -r ~/Videos     scan recursively
    """
    if ctx.invoked_subcommand is None:
        interactive_mode()


@cli.command("interactive", hidden=True)
@click.argument("directory", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("-r", "--recursive", is_flag=True, default=False,
              help="Scan subdirectories too.")
def interactive_cmd(directory, recursive):
    """Interactively pick files in DIRECTORY and convert them."""
    interactive_mode(Path(directory), recursive=recursive)


@cli.command("cast")
@click.argument("inputs", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("-f", "--format", "fmt", required=True,
              type=click.Choice(sorted(FORMATS.keys()), case_sensitive=False),
              help="Target format.")
@click.option("-q", "--quality", default="high",
              type=click.Choice(["lossless", "high", "medium", "low"]),
              show_default=True)
@click.option("-s", "--speed", default="slow", show_default=True,
              help="Encoding speed preset (veryslow…ultrafast).")
@click.option("-o", "--output-dir", default=None, type=click.Path())
@click.option("--suffix", default=None, help="Suffix to append to output stem.")
@click.option("--gpu", is_flag=True, default=False, help="Use NVENC GPU acceleration.")
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--ffmpeg-flag", "extra_flags", multiple=True,
              help="Raw flag forwarded to ffmpeg.")
def cast_cmd(inputs, fmt, quality, speed, output_dir, suffix, gpu, dry_run, extra_flags):
    """Convert INPUT file(s) to FORMAT (non-interactive).

    \b
    Examples:
      ncaster cast video.mov -f mp4
      ncaster cast *.mov -f mp4 -q high -s slow
      ncaster cast clip.mp4 -f gif -o ~/Desktop
      ncaster cast talk.mov -f mp3 -q high
    """
    fmt = fmt.lower()
    profile = FORMATS[fmt]
    out_ext = profile["ext"]
    files = [Path(p) for p in inputs]

    console.print(Panel.fit(
        f"[bold cyan]ncaster[/]  {len(files)} file(s) → [bold]{fmt.upper()}[/]  "
        f"quality=[yellow]{quality}[/]  speed=[yellow]{speed}[/]"
        + ("  [magenta][GPU][/]" if gpu else "")
        + ("  [dim][dry-run][/]" if dry_run else ""),
        border_style="cyan",
    ))

    results = []
    for src in files:
        stem = src.stem + (suffix or "")
        out_dir = Path(output_dir) if output_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"{stem}.{out_ext}"
        if dst.resolve() == src.resolve():
            dst = out_dir / f"{stem}_converted.{out_ext}"

        console.print(f"\n[bold]{src.name}[/] → [green]{dst}[/]")
        ok = run_conversion(src, dst, fmt, quality, speed, list(extra_flags), gpu, dry_run)

        if ok and not dry_run:
            src_mb = src.stat().st_size / 1_048_576
            dst_mb = dst.stat().st_size / 1_048_576
            ratio = dst_mb / src_mb * 100 if src_mb else 0
            console.print(
                f"  [green]Done[/]  {src_mb:.1f} MB → {dst_mb:.1f} MB  "
                f"([{'green' if ratio <= 100 else 'yellow'}]{ratio:.0f}%[/])"
            )
        elif ok:
            console.print("  [dim]dry-run OK[/]")
        else:
            console.print("  [red]Failed[/]")
        results.append((src, dst, ok))

    if len(results) > 1:
        table = Table(title="\nSummary", box=box.SIMPLE_HEAD)
        table.add_column("Input",  style="cyan",  no_wrap=True)
        table.add_column("Output", style="green", no_wrap=True)
        table.add_column("Status")
        for src, dst, ok in results:
            table.add_row(src.name, dst.name, "[green]OK[/]" if ok else "[red]FAILED[/]")
        console.print(table)


@cli.command("formats")
def formats_cmd():
    """List all supported formats and their codecs."""
    table = Table(title="Supported Formats", box=box.ROUNDED)
    table.add_column("Format", style="bold cyan")
    table.add_column("Video Codec")
    table.add_column("Audio Codec")
    table.add_column("Type")
    for fmt, p in sorted(FORMATS.items()):
        kind = "audio" if not p["vcodec"] else ("gif/image" if fmt == "gif" else "video")
        table.add_row(fmt, p["vcodec"] or "—", p["acodec"] or "—", kind)
    console.print(table)
    console.print("\n[dim]Quality:[/] lossless · high · medium · low")
    console.print("[dim]Speed:[/]   veryslow → ultrafast  (slower = better compression)")


@cli.command("info")
@click.argument("input", type=click.Path(exists=True))
def info_cmd(input):
    """Show media info for a file (via ffprobe)."""
    path = Path(input)
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries",
         "stream=codec_name,codec_type,width,height,r_frame_rate,bit_rate,sample_rate,channels",
         "-show_entries", "format=duration,size,bit_rate,format_long_name",
         "-of", "default=noprint_wrappers=0",
         str(path)],
        capture_output=True, text=True, timeout=15,
    )
    console.print(Panel(Text(result.stdout or result.stderr),
                        title=f"[bold]{path.name}[/]", border_style="blue"))


if __name__ == "__main__":
    cli()
