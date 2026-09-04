"""Тесты тулзов заказчика/поставщика без покупного ключа."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

ORG_HTML = """
<div class="registry-entry__header">
  <div class="registry-entry__header-mid__number">
    <a>ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО "СБЕРБАНК РОССИИ"</a>
  </div>
  <div class="registry-entry__body-block">
    <div class="registry-entry__body-title">ИНН</div>
    <div class="registry-entry__body-value">7707083893</div>
  </div>
  <div class="registry-entry__body-block">
    <div class="registry-entry__body-title">Местонахождение</div>
    <div class="registry-entry__body-value">117312, Г.МОСКВА, УЛ. ВАВИЛОВА, Д.19</div>
  </div>
</div>
"""


@pytest.mark.asyncio
async def test_customer_history_official_without_pro_key(monkeypatch) -> None:
    import mcp_zakupki.server as srv

    monkeypatch.delenv("MCP_ZAKUPKI_API_KEY", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_ATOMNO_API_KEY", raising=False)
    with respx.mock(assert_all_called=False) as router:
        router.get("https://zakupki.gov.ru/epz/organization/search/results.html").mock(
            return_value=Response(200, text=ORG_HTML)
        )
        router.get("https://zakupki.gov.ru/epz/dishonestsupplier/search/results.html").mock(
            return_value=Response(200, text='<p class="noRecords">Поиск не дал результатов</p>')
        )
        result = await srv.get_customer_history(inn="7707083893")
    assert result.get("error") is None
    assert result["org_inn"] == "7707083893"
    assert result["coverage"] == "identity"
    assert result["source_provider"] == "eis_public"
    assert "СБЕРБАНК" in (result.get("short_name") or "")


@pytest.mark.asyncio
async def test_supplier_stats_refuses_without_paid_source(monkeypatch) -> None:
    import mcp_zakupki.server as srv

    monkeypatch.delenv("MCP_ZAKUPKI_API_KEY", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_ATOMNO_API_KEY", raising=False)
    monkeypatch.delenv("MCP_ZAKUPKI_DAMIA_KEY", raising=False)
    result = await srv.get_supplier_stats(inn="7707083893")
    assert result["error"] == "provider_unavailable"
    assert "машинного обмена" in result["message_ru"]


@pytest.mark.asyncio
async def test_customer_history_with_pro_key_uses_hosted(monkeypatch) -> None:
    import os

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
