"""Audio enhancement: extract audio from a video, reduce background noise, and
clarify the voice — all via FFmpeg's audio filter chain.

The chain runs in two stages:

  1. *denoise* — strength chosen by profile (light / standard / aggressive),
     using ``afftdn`` (FFT denoiser) and, for the aggressive profile, an extra
     ``anlmdn`` pass.
  2. *voice* — band-limit to the vocal range, even out dynamics with a gentle
     compressor, and normalize loudness to a broadcast target (EBU R128).

Either stage can be turned off independently.
"""

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

from . import library
from .config import AUDIO_BITRATE, FORMATS
from .console import console
from .convert import parse_progress_us
from .probe import probe_duration

# Noise-reduction strength per profile. ``afftdn`` nf is in dB: more negative =
# more aggressive. The aggressive profile prepends a non-local-means pass.
ENHANCE_PROFILES = {
    "light": {
        "label": "light      — gentle cleanup, keeps the most natural tone",
        "denoise": ["afftdn=nf=-20"],
    },
    "standard": {
        "label": "standard   — balanced noise removal + clarity  (recommended)",
        "denoise": ["afftdn=nf=-25"],
    },
    "aggressive": {
        "label": "aggressive — strong removal for noisy/echoey recordings",
        "denoise": ["anlmdn=s=0.0001:p=0.002:r=0.006", "afftdn=nf=-32"],
    },
}

# Applied when voice enhancement is on: trim sub/ultra-sonic content, tame
# dynamics, then normalize loudness for a consistent, present voice.
VOICE_FILTERS = [
    "highpass=f=80",
    "lowpass=f=12000",
    "acompressor=threshold=-18dB:ratio=3:attack=20:release=250",
    "loudnorm=I=-16:TP=-1.5:LRA=11",
]

# Audio-only targets you can render the enhanced result to.
ENHANCE_FORMATS = [f for f, p in FORMATS.items() if p["vcodec"] is None]


def build_filter_chain(profile: str, denoise: bool, voice: bool) -> str:
    """Assemble the ``-af`` filter string for the requested profile/toggles."""
    filters: list[str] = []
    if denoise:
        filters += ENHANCE_PROFILES[profile]["denoise"]
    if voice:
        filters += VOICE_FILTERS
    return ",".join(filters)


def build_enhance_cmd(
    src: Path, dst: Path, profile: str, fmt: str,
    denoise: bool, voice: bool, quality: str, keep_video: bool,
) -> list[str]:
    """Build the ffmpeg argument list for one enhancement pass."""
    chain = build_filter_chain(profile, denoise, voice)
    cmd = ["ffmpeg", "-y", "-i", str(src)]

    if keep_video:
        # Re-encode audio with the filter chain, copy the video stream as-is.
        cmd += ["-c:v", "copy"]
        cmd += ["-af", chain] if chain else []
        cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE.get(quality, "192k")]
    else:
        profile_codec = FORMATS[fmt]
        cmd += ["-vn"]
        cmd += ["-af", chain] if chain else []
        cmd += ["-c:a", profile_codec["acodec"]]
        cmd += profile_codec.get("audio_flags", [])
        if profile_codec["acodec"] not in ("flac", "pcm_s16le"):
            cmd += ["-b:a", AUDIO_BITRATE.get(quality, "192k")]

    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def run_enhance(
    src: Path, dst: Path, profile: str = "standard", fmt: str = "mp3",
    denoise: bool = True, voice: bool = True, quality: str = "high",
    keep_video: bool = False, dry_run: bool = False,
) -> bool:
    """Run a single enhancement pass with a live progress bar. Returns success."""
    cmd = build_enhance_cmd(src, dst, profile, fmt, denoise, voice, quality, keep_video)

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

    try:
        library.record_enhance(src, dst, profile, denoise, voice, keep_video)
    except Exception:
        pass
    return True
