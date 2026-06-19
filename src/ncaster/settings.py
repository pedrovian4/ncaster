"""Runtime settings: load environment from .env files and manage the OpenAI key.

Lookup order for the API key (first match wins):
1. The ``OPENAI_API_KEY`` (or legacy ``OPEN_AI_API_KEY``) environment variable.
2. The per-user config file ``~/.config/ncaster/.env``.
3. A ``.env`` in the current working directory.

When no key is configured we can prompt the user once and persist it to the
per-user config file so it is picked up on every later run.
"""

import os
from pathlib import Path

import questionary
from rich.panel import Panel

from .console import console

CANONICAL_KEY = "OPENAI_API_KEY"
LEGACY_KEYS = ("OPEN_AI_API_KEY",)

USER_CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "ncaster"
)
USER_ENV_FILE = USER_CONFIG_DIR / ".env"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip("'\"")
    return values


def load_env() -> None:
    """Populate ``os.environ`` from the user and local .env files.

    Existing environment variables always win, so an explicit export overrides
    the stored config.
    """
    for path in (USER_ENV_FILE, Path.cwd() / ".env"):
        for key, val in _parse_env_file(path).items():
            os.environ.setdefault(key, val)


def get_openai_key() -> str | None:
    key = os.environ.get(CANONICAL_KEY)
    if key:
        return key
    for legacy in LEGACY_KEYS:
        if os.environ.get(legacy):
            return os.environ[legacy]
    return None


def save_openai_key(key: str) -> Path:
    """Persist the key to the per-user config file with private permissions."""
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = _parse_env_file(USER_ENV_FILE)
    existing[CANONICAL_KEY] = key
    lines = [f"{k}={v}" for k, v in existing.items()]
    USER_ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        USER_ENV_FILE.chmod(0o600)
    except OSError:
        pass
    os.environ[CANONICAL_KEY] = key
    return USER_ENV_FILE


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:5]}…{key[-4:]}"


def prompt_for_openai_key() -> str | None:
    """Interactively ask for the key and save it. Returns the key or None."""
    console.print(Panel.fit(
        "[bold]OpenAI API key required[/]\n\n"
        "ncaster's AI-assisted features need an OpenAI API key.\n"
        "Get one at [cyan]https://platform.openai.com/api-keys[/]\n"
        f"It will be saved to [dim]{USER_ENV_FILE}[/] (only you can read it).",
        border_style="yellow",
    ))
    key = questionary.password("Paste your OpenAI API key:").ask()
    if not key:
        console.print("[dim]No key entered.[/]")
        return None
    key = key.strip()
    if not key.startswith("sk-"):
        console.print("[yellow]Warning:[/] OpenAI keys usually start with 'sk-'. Saving anyway.")
    path = save_openai_key(key)
    console.print(f"[green]Saved[/] → [dim]{path}[/]")
    return key


def ensure_openai_key(interactive: bool = True) -> str | None:
    """Return a configured key, prompting for one if missing and possible."""
    key = get_openai_key()
    if key:
        return key
    if interactive:
        return prompt_for_openai_key()
    return None
