"""Play rendered previews in the terminal.

Video goes through ``mpv`` with a terminal video output:

* ``kitty`` graphics protocol when running directly in kitty/ghostty/wezterm;
* ``tct`` (true-color half blocks) everywhere else, including inside tmux,
  where kitty graphics don't pass through mpv reliably.

Override with ``NCASTER_PREVIEW_VO`` (``kitty``, ``tct``, ``sixel``, or
``window`` for a regular mpv window). Without mpv we fall back to showing a
contact sheet with ``chafa``.

When the caller doesn't own a terminal (the MCP server: stdout is the protocol
pipe), :func:`open_external` plays it in a new tmux pane or an mpv window.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

VO_ENV = "NCASTER_PREVIEW_VO"
OPEN_ENV = "NCASTER_PREVIEW_OPEN"  # pane | window | none (for open_external)


def pick_vo() -> str:
    forced = os.environ.get(VO_ENV, "").strip().lower()
    if forced:
        return forced
    if os.environ.get("TMUX"):
        return "tct"
    term = os.environ.get("TERM", "")
    if term == "xterm-kitty" or os.environ.get("TERM_PROGRAM", "").lower() in ("ghostty", "wezterm"):
        return "kitty"
    return "tct"


def mpv_cmd(path: Path, vo: str | None = None, loop: bool = False) -> list[str]:
    vo = vo or pick_vo()
    cmd = ["mpv", "--really-quiet", "--no-terminal" if vo == "window" else "--terminal=yes",
           "--keep-open=no", "--osd-level=1"]
    if loop:
        cmd.append("--loop-file=inf")
    if vo != "window":
        cmd.append(f"--vo={vo}")
        if vo == "tct":
            cmd.append("--vo-tct-algo=half-blocks")
    cmd.append(str(path))
    return cmd


def play_in_terminal(path: Path, sheet: Path | None = None, loop: bool = False) -> bool:
    """Block while the preview plays in this terminal. Returns False if nothing could play."""
    if shutil.which("mpv"):
        vo = pick_vo()
        try:
            subprocess.run(mpv_cmd(path, vo, loop))
        except KeyboardInterrupt:
            pass
        if vo != "window":
            # tct/kitty leave the frame on screen; clear it.
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
        return True
    if sheet and shutil.which("chafa"):
        subprocess.run(["chafa", "--size", f"{shutil.get_terminal_size().columns}x40", str(sheet)])
        return True
    return False


def open_external(path: Path) -> str:
    """Play ``path`` without touching the caller's terminal. Returns how it was opened."""
    mode = os.environ.get(OPEN_ENV, "").strip().lower() or ("pane" if os.environ.get("TMUX") else "window")
    if mode == "none" or not shutil.which("mpv"):
        return "none"
    if mode == "pane" and os.environ.get("TMUX") and shutil.which("tmux"):
        inner = " ".join(shlex.quote(a) for a in mpv_cmd(path, os.environ.get(VO_ENV) or "tct"))
        r = subprocess.run(["tmux", "split-window", "-h", "-l", "45%", "-d", inner],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return "tmux pane"
    subprocess.Popen(mpv_cmd(path, "window"), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return "mpv window"
