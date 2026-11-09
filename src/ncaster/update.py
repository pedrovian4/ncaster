"""Self-update: pull the latest ncaster from GitHub and reinstall it in place.

ncaster is normally installed as a standalone tool (``uv tool`` or ``pipx``) or
into a plain virtualenv with ``pip``. This module detects whichever manager owns
the current install and reruns it against the GitHub repository, preserving the
optional extras (``transcribe``, ``ai``) that are already present.
"""

import shutil
import subprocess
import sys

from . import __version__
from .console import console
from .editing import openai_available
from .transcribe import faster_whisper_available

# HTTPS so it works without configured SSH keys.
GIT_URL = "git+https://github.com/pedrovian4/ncaster.git"


def installed_extras() -> list[str]:
    """Optional extras detected in the current install, to carry over on update."""
    extras = []
    if faster_whisper_available():
        extras.append("transcribe")
    if openai_available():
        extras.append("ai")
    return extras


def build_spec(ref: str | None) -> str:
    """A pip/uv requirement string targeting the GitHub repo (optionally a ref)."""
    extras = installed_extras()
    extra_str = f"[{','.join(extras)}]" if extras else ""
    url = GIT_URL + (f"@{ref}" if ref else "")
    return f"ncaster{extra_str} @ {url}"


def detect_installer() -> tuple[str, list[str]] | None:
    """Return ``(manager_name, base_command)`` for whichever owns this install.

    Tries the tool managers that own a standalone install first (uv, pipx), then
    falls back to pip in the running interpreter. Returns None if none is usable.
    """
    if shutil.which("uv"):
        try:
            r = subprocess.run(["uv", "tool", "list"], capture_output=True, text=True, timeout=15)
            if "ncaster" in r.stdout:
                return "uv", ["uv", "tool", "install", "--reinstall"]
        except Exception:
            pass

    if shutil.which("pipx"):
        try:
            r = subprocess.run(["pipx", "list", "--short"], capture_output=True, text=True, timeout=15)
            if "ncaster" in r.stdout:
                return "pipx", ["pipx", "install", "--force"]
        except Exception:
            pass

    # Fallback: upgrade in the current environment via pip.
    return "pip", [sys.executable, "-m", "pip", "install", "--upgrade"]


def _installed_version() -> str | None:
    """Query the freshly-installed ncaster's version via its entry point."""
    exe = shutil.which("ncaster")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
        # "ncaster, version 1.2.0" → "1.2.0"
        return r.stdout.strip().rsplit(" ", 1)[-1] or None
    except Exception:
        return None


def run_update(ref: str | None = None, dry_run: bool = False) -> bool:
    """Reinstall ncaster from GitHub. Returns success."""
    detected = detect_installer()
    if detected is None:
        console.print("[red]Could not find a package manager (uv, pipx, or pip) to update with.[/]")
        return False

    manager, base_cmd = detected
    spec = build_spec(ref)
    cmd = base_cmd + [spec]

    console.print(
        f"[bold]Updating via [cyan]{manager}[/][/]  "
        f"(current [yellow]{__version__}[/])"
        + (f"  ref=[magenta]{ref}[/]" if ref else "")
    )
    console.print(f"[dim]$ {' '.join(cmd)}[/]")

    if dry_run:
        console.print("[dim]dry-run — not running.[/]")
        return True

    try:
        proc = subprocess.run(cmd)
    except FileNotFoundError:
        console.print(f"[red]{manager} is not available on PATH.[/]")
        return False

    if proc.returncode != 0:
        console.print(f"[red]Update failed[/] ({manager} exited {proc.returncode}).")
        return False

    new_version = _installed_version()
    if new_version and new_version != __version__:
        console.print(f"[green]Updated[/]  {__version__} → [bold]{new_version}[/]")
    elif new_version:
        console.print(f"[green]Already up to date[/]  ([bold]{new_version}[/])")
    else:
        console.print("[green]Update complete.[/]")
    return True
