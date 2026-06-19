import importlib
from pathlib import Path


def _fresh_settings(monkeypatch, tmp_path: Path):
    """Reload settings with HOME/XDG pointed at a temp dir and a clean env."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_AI_API_KEY", raising=False)
    import ncaster.settings as settings
    return importlib.reload(settings)


def test_get_key_prefers_canonical(monkeypatch, tmp_path):
    settings = _fresh_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-canonical")
    monkeypatch.setenv("OPEN_AI_API_KEY", "sk-legacy")
    assert settings.get_openai_key() == "sk-canonical"


def test_get_key_falls_back_to_legacy(monkeypatch, tmp_path):
    settings = _fresh_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("OPEN_AI_API_KEY", "sk-legacy")
    assert settings.get_openai_key() == "sk-legacy"


def test_save_and_reload_key(monkeypatch, tmp_path):
    settings = _fresh_settings(monkeypatch, tmp_path)
    path = settings.save_openai_key("sk-stored-123")
    assert path.is_file()
    # parsing the file back yields the key
    assert settings._parse_env_file(path)["OPENAI_API_KEY"] == "sk-stored-123"


def test_load_env_does_not_override_existing(monkeypatch, tmp_path):
    settings = _fresh_settings(monkeypatch, tmp_path)
    settings.save_openai_key("sk-from-file")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    settings.load_env()
    assert settings.get_openai_key() == "sk-from-env"


def test_load_env_reads_user_file(monkeypatch, tmp_path):
    settings = _fresh_settings(monkeypatch, tmp_path)
    settings.save_openai_key("sk-from-file")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings.load_env()
    assert settings.get_openai_key() == "sk-from-file"


def test_mask_key():
    settings_mod = importlib.import_module("ncaster.settings")
    assert settings_mod.mask_key("sk-abcdefgh1234") == "sk-ab…1234"
    assert settings_mod.mask_key("short") == "*****"
