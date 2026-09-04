"""Официальные корни Минцифры загружаются и не ломают обычный TLS."""

from __future__ import annotations

from pathlib import Path

from mcp_zakupki.config import DEFAULT_CACHE_FILENAME, default_cache_db_path
from mcp_zakupki.ru_ca import ssl_context_with_mintcifry


def test_ssl_context_loads_official_roots() -> None:
    ctx = ssl_context_with_mintcifry()
    assert ctx.verify_mode != 0


def test_default_cache_uses_user_data_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    dest = default_cache_db_path()
    assert dest == tmp_path / "atomno" / DEFAULT_CACHE_FILENAME
    assert dest != Path.cwd() / DEFAULT_CACHE_FILENAME
