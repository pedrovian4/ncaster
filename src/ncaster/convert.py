"""FFmpeg-based conversion: command construction and progress-tracked execution."""

import re
import subprocess
import tempfile
from pathlib import Path

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from .config import AUDIO_BITRATE, FORMATS, PRESETS, QUALITY_CRF
from .console import console
from .probe import probe_duration


def build_ffmpeg_cmd(
    src: Path, dst: Path, fmt: str, quality: str, speed: str,
    extra_flags: list[str], gpu: bool,
) -> list[str]:
    """Build the ffmpeg argument list for one conversion."""
    profile = FORMATS[fmt]
    vcodec = profile["vcodec"]
    acodec = profile["acodec"]

    cmd = ["ffmpeg", "-y", "-i", str(src)]

    if vcodec:
        vcodec_actual = (
            {"libx264": "h264_nvenc", "libx265": "hevc_nvenc"}.get(vcodec, vcodec)
            if gpu else vcodec
        )
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
    """Parse an ``out_time_us=`` line from ffmpeg ``-progress`` output into seconds."""
    m = re.match(r"out_time_us=(\d+)", line)
    return int(m.group(1)) / 1_000_000 if m else None


def run_conversion(
    src: Path, dst: Path, fmt: str, quality: str, speed: str,
    extra_flags: list[str], gpu: bool, dry_run: bool,
) -> bool:
    """Run a single conversion with a live progress bar. Returns success."""
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
