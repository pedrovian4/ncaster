"""Command-line interface: the click command group and subcommands."""

import subprocess
from pathlib import Path

import click
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import (
    FORMATS,
    QUALITIES,
    TRANSCRIBE_LANGS,
    TRANSCRIPT_FORMATS,
    WHISPER_MODELS,
)
from .console import console
from .convert import run_conversion
from .interactive import interactive_mode
from .transcribe import faster_whisper_available, run_transcription, warn_missing_whisper


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
@click.version_option(__version__, prog_name="ncaster")
@click.pass_context
def cli(ctx):
    """ncaster — convert media between formats and transcribe speech locally.

    \b
    Interactive mode (fuzzy-pick files, then choose an action):
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
    """Interactively pick files in DIRECTORY and act on them."""
    interactive_mode(Path(directory), recursive=recursive)


@cli.command("cast")
@click.argument("inputs", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("-f", "--format", "fmt", required=True,
              type=click.Choice(sorted(FORMATS.keys()), case_sensitive=False),
              help="Target format.")
@click.option("-q", "--quality", default="high",
              type=click.Choice(QUALITIES), show_default=True)
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
    out_ext = FORMATS[fmt]["ext"]
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
        table.add_column("Input", style="cyan", no_wrap=True)
        table.add_column("Output", style="green", no_wrap=True)
        table.add_column("Status")
        for src, dst, ok in results:
            table.add_row(src.name, dst.name, "[green]OK[/]" if ok else "[red]FAILED[/]")
        console.print(table)


@cli.command("transcribe")
@click.argument("inputs", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("-l", "--language", default="auto",
              type=click.Choice(list(TRANSCRIBE_LANGS.keys()), case_sensitive=False),
              show_default=True, help="Spoken language (auto-detect, en, pt).")
@click.option("-m", "--model", "model_size", default="small",
              type=click.Choice(WHISPER_MODELS, case_sensitive=False),
              show_default=True, help="Whisper model size.")
@click.option("-f", "--format", "out_fmt", default="srt",
              type=click.Choice(TRANSCRIPT_FORMATS, case_sensitive=False),
              show_default=True, help="Transcript output format.")
@click.option("-o", "--output-dir", default=None, type=click.Path())
def transcribe_cmd(inputs, language, model_size, out_fmt, output_dir):
    """Transcribe speech in INPUT file(s) to text/subtitles via local Whisper.

    \b
    Examples:
      ncaster transcribe talk.mp4
      ncaster transcribe aula.mov -l pt -f srt
      ncaster transcribe podcast.mp3 -l en -m medium -f txt
    """
    if not faster_whisper_available():
        warn_missing_whisper()
        raise SystemExit(1)

    files = [Path(p) for p in inputs]
    console.print(Panel.fit(
        f"[bold cyan]ncaster transcribe[/]  {len(files)} file(s)  "
        f"lang=[yellow]{language}[/]  model=[yellow]{model_size}[/]  "
        f"→ [bold]{out_fmt}[/]",
        border_style="cyan",
    ))
    console.print("[dim]Loading Whisper model (first run downloads it)…[/]\n")

    results = []
    for src in files:
        out_dir = Path(output_dir) if output_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"{src.stem}.{out_fmt}"
        console.print(f"[bold]{src.name}[/] → [green]{dst}[/]")
        ok = run_transcription(src, dst, model_size, language, out_fmt)
        if not ok:
            console.print("  [red]Failed[/]")
        results.append(ok)

    if len(results) > 1:
        done = sum(1 for ok in results if ok)
        console.print(f"\n[bold]Done:[/] {done}/{len(results)} transcribed.")


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
