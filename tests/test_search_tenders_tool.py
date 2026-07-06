"""Smoke-тесты `search_tenders` тулза c respx-моками."""

from __future__ import annotations

import os

import pytest
import respx
from httpx import Response

from mcp_zakupki.config import AppConfig
from mcp_zakupki.context import ServiceContext
from mcp_zakupki.errors import ProviderUnavailableError, ValidationError
from mcp_zakupki.tools import search_tenders


@pytest.mark.asyncio
class TestSearchTenders:
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

    async def test_no_keys_yields_provider_unavailable(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            with pytest.raises(ProviderUnavailableError):
                await search_tenders(ctx, query="портал")

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
