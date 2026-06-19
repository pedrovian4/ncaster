"""AI-assisted editing drafts built on top of a local transcript.

The transcript is produced locally by Whisper; only the resulting *text* is
sent to OpenAI to propose an editing draft. No audio or video ever leaves the
machine. Uses the lightest chat model by default (gpt-4o-mini).
"""

import json
from pathlib import Path

from .console import console
from .probe import human_duration
from .settings import get_openai_key
from .transcribe import format_timestamp

DEFAULT_MODEL = "gpt-4o-mini"

# Editing styles. Each entry drives a different draft generator.
EDITING_STYLES = {
    "narrative-visual": {
        "label": "Narrative Visual — overlay images over the speaker (Shorts / Reels / TikTok)",
        "short": "Narrative Visual",
        "description": (
            "For each spoken segment, suggest a B-roll clip, image, graphic, or "
            "text overlay to display on top of the talking-head speaker."
        ),
    },
}

# Send this many segments per OpenAI request to keep payloads small.
_BATCH_SIZE = 40

_LANG_NAMES = {"en": "English", "pt": "Portuguese"}


def openai_available() -> bool:
    try:
        import openai  # noqa: F401
        return True
    except ImportError:
        return False


def warn_missing_openai() -> None:
    from rich.panel import Panel
    console.print(Panel.fit(
        "[red]OpenAI support is not installed.[/]\n\n"
        "Add it with one of:\n"
        "  [cyan]uv tool install \"ncaster[ai]\" --reinstall[/]\n"
        "  [cyan]uv tool install \".[ai]\" --reinstall[/]  [dim](from a clone)[/]",
        border_style="red",
    ))


def _narrative_visual_messages(batch: list[dict], language: str) -> list[dict]:
    lang_name = _LANG_NAMES.get(language, "the transcript's language")
    system = (
        "You are an assistant editor for short-form vertical videos "
        "(YouTube Shorts, Instagram Reels, TikTok). The user gives you a "
        "transcript of someone talking to camera, split into timestamped "
        "segments. For the 'Narrative Visual' style you propose, for each "
        "segment, a single visual (B-roll clip, image, graphic, or text "
        "overlay) to show ON TOP of the speaker to illustrate or emphasize "
        "what they say at that moment.\n"
        "Rules:\n"
        "- One concrete, vivid suggestion per segment, max ~18 words.\n"
        "- Describe WHAT to show, not how to film it; do not generate the image.\n"
        "- Make it specific to the words of that segment; vary ideas, avoid repeats.\n"
        "- If a segment is filler with nothing to illustrate, return "
        "\"keep on speaker, no overlay\".\n"
        f"- Write the visual descriptions in {lang_name}.\n"
        "Respond ONLY with JSON of the form "
        "{\"suggestions\": [{\"index\": <int>, \"visual\": <string>}]} "
        "covering every index you are given."
    )
    user = json.dumps(
        {"segments": [{"index": s["index"], "text": s["text"].strip()} for s in batch]},
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def generate_editing_draft(
    segments: list[dict],
    style: str = "narrative-visual",
    model: str | None = None,
    language: str = "auto",
) -> list[dict] | None:
    """Return segments enriched with a ``visual`` suggestion, or None on failure."""
    key = get_openai_key()
    if not key:
        console.print("[red]No OpenAI API key configured.[/] Run [cyan]ncaster config[/].")
        return None
    if style not in EDITING_STYLES:
        console.print(f"[red]Unknown editing style:[/] {style}")
        return None

    from openai import OpenAI
    client = OpenAI(api_key=key)
    model = model or DEFAULT_MODEL

    indexed = [{**s, "index": i} for i, s in enumerate(segments)]
    visuals: dict[int, str] = {}

    for start in range(0, len(indexed), _BATCH_SIZE):
        batch = indexed[start:start + _BATCH_SIZE]
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=_narrative_visual_messages(batch, language),
                response_format={"type": "json_object"},
                temperature=0.8,
            )
            data = json.loads(resp.choices[0].message.content)
            for item in data.get("suggestions", []):
                visuals[int(item["index"])] = str(item["visual"]).strip()
        except Exception as e:
            console.print(f"  [red]OpenAI error:[/] {e}")
            return None

    return [
        {**s, "visual": visuals.get(i, "keep on speaker, no overlay")}
        for i, s in enumerate(segments)
    ]


def write_draft_md(
    items: list[dict],
    dst: Path,
    style: str,
    video_name: str,
    language: str,
    model: str,
) -> None:
    meta = EDITING_STYLES.get(style, {})
    total = items[-1]["end"] if items else 0
    lines = [
        f"# Editing draft — {meta.get('short', style)}",
        "",
        f"- **Source:** {video_name}",
        f"- **Style:** {meta.get('label', style)}",
        f"- **Language:** {language}",
        f"- **Segments:** {len(items)}  ·  **Duration:** {human_duration(total)}",
        f"- **Generated with:** OpenAI {model} (overlay suggestions only)",
        "",
        "> Transcription is local (Whisper). The overlay column is an AI draft —"
        " review and adjust before editing.",
        "",
    ]
    for s in items:
        ts = f"{format_timestamp(s['start'])} → {format_timestamp(s['end'])}"
        lines += [
            f"## {ts}",
            f"🗣️  {s['text'].strip()}",
            "",
            f"🎬  **Overlay:** {s['visual']}",
            "",
        ]
    dst.write_text("\n".join(lines), encoding="utf-8")
