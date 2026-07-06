"""Тесты hosted tier gating для Pro-тулов и fair-use лимита BYOK."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_customer_history_requires_pro_key(monkeypatch) -> None:
    import mcp_zakupki.server as srv

    monkeypatch.delenv("MCP_ZAKUPKI_API_KEY", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_ATOMNO_API_KEY", raising=False)
    result = await srv.get_customer_history(inn="7707083893")
    assert result["error"] == "pro_required"
    assert "MCP_ZAKUPKI_ATOMNO_API_KEY" in result["message_ru"] or "Atomno" in result["message_ru"]


@pytest.mark.asyncio
async def test_supplier_stats_requires_pro_key(monkeypatch) -> None:
    import mcp_zakupki.server as srv

    monkeypatch.delenv("MCP_ZAKUPKI_API_KEY", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_ATOMNO_API_KEY", raising=False)
    result = await srv.get_supplier_stats(inn="7707083893")
    assert result["error"] == "pro_required"


@pytest.mark.asyncio
async def test_customer_history_with_pro_key_hosted_unavailable(monkeypatch) -> None:
    import os

    import respx
    from httpx import Response

    import mcp_zakupki.server as srv
    from mcp_zakupki.config import AppConfig
    from mcp_zakupki.context import ServiceContext

    monkeypatch.setenv("MCP_ZAKUPKI_API_KEY", "test-pro-key")
    env = dict(os.environ)
    env["MCP_ZAKUPKI_CACHE_DB"] = "/tmp/tier_test.sqlite"
    cfg = AppConfig.from_env(env)
    assert cfg.hosted_mode_enabled
    ctx = ServiceContext(cfg)
    await ctx.__aenter__()
    srv._ctx = ctx
    try:
        with respx.mock(assert_all_called=False) as router:
            router.post("https://api.atomno-mcp.ru/zakupki/v1/customer-history").mock(
                return_value=Response(503)
            )
            result = await srv.get_customer_history(inn="7707083893")
        assert result["error"] == "hosted_unavailable"
    finally:
        await ctx.__aexit__(None, None, None)
        srv._ctx = None
