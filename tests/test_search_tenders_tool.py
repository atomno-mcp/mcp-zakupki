"""Smoke-тесты `search_tenders` тулза c respx-моками."""

from __future__ import annotations

import os

import pytest
import respx
from httpx import Response

from mcp_zakupki.cache import make_args_hash
from mcp_zakupki.config import AppConfig
from mcp_zakupki.context import ServiceContext
from mcp_zakupki.errors import ValidationError
from mcp_zakupki.schemas import SearchResult, TenderSearchFilter
from mcp_zakupki.tools import search_tenders

SEARCH_HTML = """
<div class="registry-entry__form">
  <div class="registry-entry__header">
    <div class="registry-entry__header-top__title">44-ФЗ</div>
    <div class="registry-entry__header-mid__number">
      <a href="/epz/order/notice/ea20/view/common-info.html?regNumber=0131200001026005946">
        0131200001026005946
      </a>
    </div>
  </div>
  <div class="registry-entry__body-value">Поставка бумаги</div>
</div>
"""


@pytest.mark.asyncio
class TestSearchTenders:
    async def test_old_shared_cache_cannot_supply_open_result(self, app_config):
        filter_ = TenderSearchFilter(query="synthetic")
        legacy_key = make_args_hash(filter_.model_dump(mode="json"))
        async with ServiceContext(app_config) as ctx:
            await ctx.cache.put_search(
                legacy_key, filter_.model_dump(mode="json"),
                SearchResult(query_echo={"synthetic": "old hosted result"}, tenders=[]).model_dump(mode="json"),
                provider="eis_public", next_page_token=None, total_estimated=None,
            )
            with respx.mock as router:
                route = router.get("https://zakupki.gov.ru/epz/order/extendedsearch/results.html").respond(
                    200, text=SEARCH_HTML,
                )
                first = await search_tenders(ctx, query="synthetic")
                second = await search_tenders(ctx, query="synthetic")
                assert first.tenders and not first.search_meta.cache_hit
                assert second.search_meta.cache_hit
                assert route.call_count == 1

    async def test_invalid_inn_rejected(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            with pytest.raises(ValidationError):
                await search_tenders(ctx, customer_inn="bad-inn")

    async def test_invalid_okpd_rejected(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            with pytest.raises(ValidationError):
                await search_tenders(ctx, okpd2_codes=["abc"])

    async def test_invalid_price_range_rejected(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            with pytest.raises(ValidationError):
                await search_tenders(ctx, price_min_rub=500, price_max_rub=100)

    async def test_official_source_without_keys(self, app_config) -> None:
        with respx.mock(assert_all_called=False) as router:
            router.get("https://zakupki.gov.ru/epz/order/extendedsearch/results.html").mock(
                return_value=Response(200, text=SEARCH_HTML),
            )
            async with ServiceContext(app_config) as ctx:
                result = await search_tenders(ctx, query="бумага")
        assert result.tenders
        assert result.tenders[0].reg_number == "0131200001026005946"
        assert result.search_meta.source == "eis_public"

    async def test_damia_with_key_returns_results(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("MCP_ZAKUPKI_DAMIA_KEY", "test-key")
        monkeypatch.setenv("MCP_ZAKUPKI_PROVIDERS", "damia")
        cfg = AppConfig.from_env(dict(os.environ) | {
            "MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "cache.sqlite"),
        })
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.damia.ru/zakupki/zsearch").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "reg": "0173100007426000018",
                                "title": "Разработка веб-портала",
                                "fz": "44",
                                "okpd": "62.01",
                                "customer_inn": "7707083893",
                                "customer_name": "ТЕСТЕР",
                                "region": "66",
                                "price": 1500000.0,
                                "smp": True,
                            }
                        ],
                        "total": 1,
                    },
                )
            )
            async with ServiceContext(cfg) as ctx:
                result = await search_tenders(
                    ctx,
                    query="портал",
                    okpd2_codes=["62.01"],
                    regions=["66"],
                )
        assert result.tenders
        assert result.tenders[0].reg_number == "0173100007426000018"
        assert result.search_meta.cache_hit is False

    async def test_second_call_hits_cache(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("MCP_ZAKUPKI_DAMIA_KEY", "test-key")
        monkeypatch.setenv("MCP_ZAKUPKI_PROVIDERS", "damia")
        cfg = AppConfig.from_env(dict(os.environ) | {
            "MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "cache.sqlite"),
        })
        with respx.mock(assert_all_called=False) as router:
            route = router.get("https://api.damia.ru/zakupki/zsearch").mock(
                return_value=Response(
                    200,
                    json={"items": [], "total": 0},
                )
            )
            async with ServiceContext(cfg) as ctx:
                first = await search_tenders(ctx, query="x")
                second = await search_tenders(ctx, query="x")
        assert first.search_meta.cache_hit is False
        assert second.search_meta.cache_hit is True
        # 2-й вызов не должен задеть upstream
        assert route.call_count == 1
