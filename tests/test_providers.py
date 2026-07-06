"""Тесты провайдеров через httpx-моки (respx)."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from mcp_zakupki.config import AppConfig, ProvidersConfig
from mcp_zakupki.errors import (
    AuthFailedError,
    NotFoundError,
    ProviderUnavailableError,
)
from mcp_zakupki.providers import (
    DamiaProvider,
    GosplanProvider,
    HtmlFallbackProvider,
    NavodkiProvider,
    ProviderResolver,
)
from mcp_zakupki.providers.base import ProviderCapability
from mcp_zakupki.schemas import LawType, TenderSearchFilter


def _cfg(**overrides) -> AppConfig:
    base = AppConfig.from_env({"MCP_ZAKUPKI_CACHE_DB": "/tmp/test.sqlite"})
    chain_override = overrides.pop("chain", None)
    if overrides or chain_override is not None:
        providers = ProvidersConfig(**{**base.providers.__dict__, **overrides})
        base = AppConfig(
            cache_db=base.cache_db,
            providers=providers,
            chain=chain_override if chain_override is not None else base.chain,
            rps=base.rps,
            http_timeout_s=base.http_timeout_s,
            user_agent=base.user_agent,
            http_proxy=base.http_proxy,
            log_level=base.log_level,
        )
    return base


class TestProviderInfo:
    def test_damia_capabilities(self) -> None:
        p = DamiaProvider(_cfg(damia_key="test"))
        assert p.info.name == "damia"
        assert p.info.capabilities & ProviderCapability.SEARCH
        assert p.is_configured is True

    def test_damia_unconfigured(self) -> None:
        p = DamiaProvider(_cfg())
        assert p.is_configured is False

    def test_html_fallback_always_configured(self) -> None:
        p = HtmlFallbackProvider(_cfg())
        assert p.info.name == "html_fallback"
        assert p.info.capabilities & ProviderCapability.TENDER_DETAILS
        assert p.is_configured is True

    def test_gosplan_unconfigured(self) -> None:
        assert GosplanProvider(_cfg()).is_configured is False

    def test_navodki_unconfigured(self) -> None:
        assert NavodkiProvider(_cfg()).is_configured is False


@pytest.mark.asyncio
class TestDamiaSearch:
    async def test_auth_failed_without_key(self) -> None:
        async with DamiaProvider(_cfg()) as p:
            with pytest.raises(AuthFailedError):
                await p.search_tenders(TenderSearchFilter())

    async def test_search_happy_path(self) -> None:
        cfg = _cfg(damia_key="test-key")
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
                                "customer_name": "АДМИНИСТРАЦИЯ",
                                "region": "66",
                                "price": 1840000.0,
                                "smp": True,
                            }
                        ],
                        "total": 1,
                    },
                )
            )
            async with DamiaProvider(cfg) as p:
                res = await p.search_tenders(TenderSearchFilter(query="портал"))
        assert res.tenders
        assert res.tenders[0].reg_number == "0173100007426000018"
        assert res.tenders[0].law_type in (LawType.FZ_44, "44-fz")

    async def test_search_5xx_eventually_fails(self) -> None:
        cfg = _cfg(damia_key="test-key")
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.damia.ru/zakupki/zsearch").mock(
                return_value=Response(503)
            )
            async with DamiaProvider(cfg) as p:
                with pytest.raises(ProviderUnavailableError):
                    await p.search_tenders(TenderSearchFilter())

    async def test_get_tender_404_to_not_found(self) -> None:
        cfg = _cfg(damia_key="test-key")
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.damia.ru/zakupki/zakupka").mock(
                return_value=Response(404)
            )
            async with DamiaProvider(cfg) as p:
                with pytest.raises(NotFoundError):
                    await p.get_tender("0173100007426000018")


@pytest.mark.asyncio
class TestResolverFallback:
    async def test_api_chain_without_keys_raises_unavailable(self) -> None:
        # Default API-only chain: damia + gosplan + navodki без ключей.
        cfg = _cfg()
        async with ProviderResolver(cfg) as resolver:
            with pytest.raises(ProviderUnavailableError) as exc_info:
                await resolver.search_tenders(TenderSearchFilter())
            details = exc_info.value.details
            attempted_names = {a["provider"] for a in details["attempted"]}
            assert attempted_names == {"damia", "gosplan", "navodki"}
            assert "html_fallback" not in attempted_names

    async def test_get_tender_falls_to_html_when_opted_in(self, monkeypatch) -> None:
        cfg = _cfg(chain=("html_fallback",))
        with respx.mock(assert_all_called=False) as router:
            router.get(
                "https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html"
            ).mock(
                return_value=Response(
                    200,
                    text=(
                        '<?xml version="1.0" encoding="utf-8"?>'
                        "<export44>"
                        "<purchaseObjectInfo>Тестовый тендер</purchaseObjectInfo>"
                        "<INN>7707083893</INN>"
                        "<shortName>ООО ПРИМЕР</shortName>"
                        "</export44>"
                    ),
                )
            )
            async with ProviderResolver(cfg) as resolver:
                tender = await resolver.get_tender("0173100007426000018")
        assert tender.reg_number == "0173100007426000018"
        assert tender.source_provider == "html_fallback"
