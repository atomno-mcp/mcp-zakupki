"""Smoke-тесты FastMCP-сервера atomno-mcp-zakupki."""

from __future__ import annotations

import pytest


def test_server_imports() -> None:
    from mcp_zakupki import server

    assert server.mcp is not None
    assert server.mcp.name == "atomno-mcp-zakupki"


@pytest.mark.asyncio
async def test_tools_registered() -> None:
    from mcp_zakupki.server import mcp

    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "ping",
        "search_tenders",
        "get_tender",
        "get_customer_history",
        "get_supplier_stats",
        "lookup_okpd2",
    }


@pytest.mark.asyncio
async def test_lookup_okpd2_offline_happy_path() -> None:
    """lookup_okpd2 не требует сетевых провайдеров — smoke без ключей."""
    import mcp_zakupki.server as srv
    from mcp_zakupki.config import AppConfig
    from mcp_zakupki.context import ServiceContext

    cfg = AppConfig.from_env({"MCP_ZAKUPKI_CACHE_DB": "/tmp/zakupki_smoke.sqlite"})
    ctx = ServiceContext(cfg)
    await ctx.__aenter__()
    srv._ctx = ctx
    try:
        result = await srv.lookup_okpd2(query="разработка программного обеспечения", limit=3)
        assert isinstance(result, dict)
        assert result.get("error") is not True
        assert "results" in result
        assert isinstance(result["results"], list)
    finally:
        await ctx.__aexit__(None, None, None)
        srv._ctx = None
