"""Регрессии передачи фильтров и карточек между MCP и API Закупок."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastmcp import Client

from mcp_zakupki import server
from mcp_zakupki.config import AppConfig
from mcp_zakupki.context import ServiceContext
from mcp_zakupki.errors import HostedApiUnavailableError, NotFoundError, ParseError, ValidationError
from mcp_zakupki.schemas import Tender
from mcp_zakupki.tools import get_tender, search_tenders

API = "https://api.atomno-mcp.ru/zakupki/v1"


def _tender(reg_number, law_type, *, documents=()):
    return Tender(
        reg_number=reg_number,
        law_type=law_type,
        title="Синтетический предмет закупки",
        customer={"inn": "", "short_name": "Синтетический заказчик", "region_code": "66"},
        documents=list(documents),
        publish_date_only="2026-11-20",
        applications_deadline_date="2026-11-27",
        url_eis="https://zakupki.gov.ru/",
        fetched_at="2026-11-22T10:00:00Z",
        source_provider="eis_public",
    )


@pytest.fixture
async def hosted_context(tmp_path):
    cfg = AppConfig.from_env(
        {"MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "hosted.sqlite"), "MCP_ZAKUPKI_API_KEY": "synthetic"}
    )
    async with ServiceContext(cfg) as ctx:
        yield ctx


@pytest.mark.parametrize(
    ("number", "law"), [("0173100007426000018", "44-fz"), ("32616599714", "223-fz")]
)
async def test_hosted_search_preserves_filters_and_strings(hosted_context, number, law):
    body = {
        "tenders": [_tender(number, law).model_dump(mode="json")],
        "next_page_token": "3",
        "page_size": 1,
        "search_meta": {"source": "eis_public"},
    }
    with respx.mock as router:
        route = router.post(f"{API}/search").respond(200, json=body)
        result = await search_tenders(
            hosted_context, query=number, law_type=[law], regions=["66", "5"],
            limit=10, next_page_token="2",
        )
        assert json.loads(route.calls[0].request.content) == {
            "query": number, "customer_inn": None, "page": 2, "page_size": 10,
            "region_codes": ["66", "05"], "law_type": [law],
        }
        assert result.next_page_token == "3"
        assert result.tenders[0].reg_number == number
        assert result.tenders[0].customer.region_code == "66"
        assert result.tenders[0].publish_date is None
        assert result.tenders[0].model_dump(mode="json")["publish_date_only"] == "2026-11-20"


async def test_hosted_default_search_uses_api_on_each_request(hosted_context):
    with respx.mock as router:
        route = router.post(f"{API}/search").respond(200, json={"tenders": []})
        for _ in range(2):
            result = await search_tenders(hosted_context, customer_inn="7707083893")
            assert result.tenders == []
            assert not result.search_meta.cache_hit
        assert route.call_count == 2
        assert json.loads(route.calls[0].request.content) == {
            "query": None, "customer_inn": "7707083893", "page": 1, "page_size": 20,
        }


@pytest.mark.parametrize(
    "filters",
    [
        {"query": "бумага", "customer_inn": "7707083893"},
        {"status": ["applications_open"]},
        {"price_min_rub": 0},
        {"regions": ["00"]},
        {"law_type": ["223-fz", "unknown"]},
        {"law_type": ["615-pp"]},
        {"next_page_token": " 2 "},
        {"next_page_token": "2147483648"},
        {"limit": 15},
    ],
)
async def test_hosted_rejects_unapplied_filters_before_http(hosted_context, filters):
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{API}/search").respond(200, json={"tenders": []})
        with pytest.raises(ValidationError):
            await search_tenders(hosted_context, **filters)
        assert route.call_count == 0


@pytest.mark.parametrize(
    ("number", "law"), [("0173100007426000018", "44-fz"), ("32616599714", "223-fz")]
)
async def test_hosted_get_tender_preserves_number_and_documents_flag(hosted_context, number, law):
    synthetic = _tender(number, law)
    await hosted_context.cache.put_tender(
        number, synthetic.model_dump(mode="json"), provider="eis_public", is_completed=False
    )
    with respx.mock as router:
        route = router.post(f"{API}/tender").respond(200, json=synthetic.model_dump(mode="json"))
        result = await get_tender(hosted_context, reg_number=f" {number} ", include_documents=False)
        assert json.loads(route.calls[0].request.content) == {
            "reg_number": number, "include_documents": False, "include_protocols": False,
        }
        assert result.reg_number == number
        assert result.law_type == law
        assert result.documents == []
        assert result.model_dump(mode="json")["applications_deadline_date"] == "2026-11-27"


async def test_hosted_rejects_unsupported_protocol_flag(hosted_context):
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{API}/tender").respond(200)
        with pytest.raises(ValidationError, match="протоколы"):
            await get_tender(hosted_context, reg_number="32616599714", include_protocols=True)
        assert route.call_count == 0


async def test_mcp_get_tender_returns_223_card(hosted_context, monkeypatch):
    number = "32616599714"
    monkeypatch.setattr(server, "_ctx", hosted_context)
    with respx.mock as router:
        route = router.post(f"{API}/tender").respond(
            200, json=_tender(number, "223-fz").model_dump(mode="json")
        )
        async with Client(server.mcp) as client:
            result = await client.call_tool("get_tender", {"reg_number": number})
        assert route.call_count == 1
    assert result.data["reg_number"] == number
    assert result.data["law_type"] == "223-fz"
    assert result.data["publish_date_only"] == "2026-11-20"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (404, {"error": "not_found", "message_ru": "Закупка отсутствует"}, NotFoundError),
        (404, {"detail": "Not Found"}, HostedApiUnavailableError),
        (422, {"detail": []}, ValidationError),
        (200, {}, ParseError),
        (200, [], ParseError),
    ],
)
async def test_hosted_distinguishes_missing_from_invalid_or_unavailable(hosted_context, status, body, expected):
    with respx.mock:
        respx.post(f"{API}/tender").respond(status, json=body)
        with pytest.raises(expected):
            await get_tender(hosted_context, reg_number="32616599714")


async def test_hosted_rejects_a_card_for_another_notice(hosted_context):
    wrong = _tender("0173100007426000018", "44-fz")
    with respx.mock:
        respx.post(f"{API}/tender").respond(200, json=wrong.model_dump(mode="json"))
        with pytest.raises(ParseError, match="другим реестровым номером"):
            await get_tender(hosted_context, reg_number="32616599714")


async def test_hosted_search_rejects_missing_result_array(hosted_context):
    with respx.mock:
        respx.post(f"{API}/search").respond(200, json={"query_echo": {}})
        with pytest.raises(ParseError, match="обязательное поле"):
            await search_tenders(hosted_context)


async def test_hosted_search_timeout_is_not_empty_result(hosted_context):
    with respx.mock:
        respx.post(f"{API}/search").mock(side_effect=httpx.ReadTimeout)
        with pytest.raises(HostedApiUnavailableError, match="за отведённое время"):
            await search_tenders(hosted_context)


async def test_direct_tender_cache_keys_include_document_choice(app_config, monkeypatch):
    number = "32616599714"
    card = _tender(number, "223-fz")
    with_docs = _tender(
        number, "223-fz", documents=[{"title": "Извещение", "url": "https://zakupki.gov.ru/sample"}]
    )
    provider_call = AsyncMock(side_effect=[with_docs, card])
    async with ServiceContext(app_config) as ctx:
        monkeypatch.setattr(ctx.resolver, "get_tender", provider_call)
        assert (await get_tender(ctx, reg_number=number)).documents == with_docs.documents
        assert not (await get_tender(ctx, reg_number=number, include_documents=False)).documents
        assert (await get_tender(ctx, reg_number=number)).documents == with_docs.documents
    assert provider_call.await_count == 2
