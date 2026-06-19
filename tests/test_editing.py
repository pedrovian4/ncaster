import json
import sys
import types
from pathlib import Path

from ncaster.editing import (
    _narrative_visual_messages,
    generate_editing_draft,
    write_draft_md,
)

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": " The rocket lifted off at dawn."},
    {"start": 2.0, "end": 4.0, "text": " Thousands watched from the beach."},
]


def test_messages_include_indices_and_language():
    batch = [{"index": 0, "text": "hello"}, {"index": 1, "text": "world"}]
    msgs = _narrative_visual_messages(batch, "pt")
    assert msgs[0]["role"] == "system"
    assert "Portuguese" in msgs[0]["content"]
    payload = json.loads(msgs[1]["content"])
    assert [s["index"] for s in payload["segments"]] == [0, 1]


def test_write_draft_md(tmp_path: Path):
    items = [{**s, "visual": f"overlay {i}"} for i, s in enumerate(SEGMENTS)]
    dst = tmp_path / "v.draft.md"
    write_draft_md(items, dst, "narrative-visual", "v.mp4", "en", "gpt-4o-mini")
    text = dst.read_text(encoding="utf-8")
    assert "# Editing draft — Narrative Visual" in text
    assert "rocket lifted off" in text
    assert "**Overlay:** overlay 0" in text
    assert "00:00:00,000 → 00:00:02,000" in text


def _install_fake_openai(monkeypatch, response_obj):
    """Inject a fake `openai` module whose client returns canned JSON."""
    captured = {}

    class _Msg:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class _Completions:
        def create(self, **kwargs):
            captured["kwargs"] = kwargs
            return types.SimpleNamespace(choices=[_Msg(json.dumps(response_obj))])

    class _Chat:
        completions = _Completions()

    class OpenAI:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key
            self.chat = _Chat()

    fake = types.ModuleType("openai")
    fake.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    return captured


def test_generate_editing_draft_merges_suggestions(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    response = {"suggestions": [
        {"index": 0, "visual": "B-roll of a rocket launch"},
        {"index": 1, "visual": "Crowd cheering on a beach"},
    ]}
    captured = _install_fake_openai(monkeypatch, response)

    items = generate_editing_draft(SEGMENTS, style="narrative-visual",
                                   model="gpt-4o-mini", language="en")
    assert items is not None
    assert items[0]["visual"] == "B-roll of a rocket launch"
    assert items[1]["visual"] == "Crowd cheering on a beach"
    # uses the configured key and JSON response format
    assert captured["api_key"] == "sk-test"
    assert captured["kwargs"]["response_format"] == {"type": "json_object"}
    assert captured["kwargs"]["model"] == "gpt-4o-mini"


def test_generate_editing_draft_fills_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _install_fake_openai(monkeypatch, {"suggestions": [{"index": 0, "visual": "only first"}]})
    items = generate_editing_draft(SEGMENTS, language="en")
    assert items[0]["visual"] == "only first"
    assert items[1]["visual"] == "keep on speaker, no overlay"


def test_generate_editing_draft_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_AI_API_KEY", raising=False)
    # also ensure no .env on disk leaks a key into this process
    monkeypatch.setattr("ncaster.editing.get_openai_key", lambda: None)
    assert generate_editing_draft(SEGMENTS, language="en") is None
