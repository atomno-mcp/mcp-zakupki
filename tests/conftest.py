"""Общие pytest-фикстуры для tests/."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from mcp_zakupki.cache import CacheStore
from mcp_zakupki.config import AppConfig


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Изолировать тесты от пользовательского окружения и кэш-файлов."""
    monkeypatch.delenv("MCP_ZAKUPKI_LOG_LEVEL", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_DAMIA_KEY", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_GOSPLAN_KEY", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_NAVODKI_KEY", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_EIS_TOKEN", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_API_KEY", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_PROVIDERS", raising=False)
    monkeypatch.setenv("MCP_ZAKUPKI_CACHE_DB", str(tmp_path / "test_cache.sqlite"))


@pytest_asyncio.fixture
async def cache(tmp_path) -> AsyncIterator[CacheStore]:
    store = CacheStore(tmp_path / "cache.sqlite")
    await store.init()
    try:
        yield store
    finally:
        await store.close()


@pytest_asyncio.fixture
async def app_config(tmp_path) -> AppConfig:
    return AppConfig.from_env({
        "MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "cache.sqlite"),
    })
