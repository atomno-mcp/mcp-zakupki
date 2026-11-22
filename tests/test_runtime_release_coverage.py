"""Регрессии сетевого транспорта и пользовательских входов MCP."""

from __future__ import annotations

import json
from unittest.mock import Mock

import httpx
import pytest
import respx
from fastmcp import Client
from tenacity import wait_none

from mcp_zakupki import server
from mcp_zakupki.config import AppConfig
from mcp_zakupki.context import ServiceContext
from mcp_zakupki.errors import (
    AuthFailedError,
    NotFoundError,
    ParseError,
    ProviderUnavailableError,
    RateLimitedError,
)
from mcp_zakupki.providers import ProviderResolver
from mcp_zakupki.providers.http_client import HttpAdapter
from mcp_zakupki.schemas import TenderSearchFilter
from mcp_zakupki.tools import get_tender

HOSTED_API = "https://api.atomno-mcp.ru/zakupki/v1"
MOCK_API = "https://provider.example.test"


@pytest.mark.parametrize(
    ("status", "not_found", "error"),
    [
        (401, False, AuthFailedError),
        (403, False, AuthFailedError),
        (404, True, NotFoundError),
        (404, False, ParseError),
        (429, False, RateLimitedError),
        (500, False, ProviderUnavailableError),
    ],
)
async def test_provider_http_failure_keeps_its_meaning(app_config, status, not_found, error):
    with respx.mock as router:
        route = router.get(f"{MOCK_API}/notice").respond(status)
        async with HttpAdapter(app_config, base_url=MOCK_API) as adapter:
            with pytest.raises(error) as failure:
                await adapter.get_text(
                    "/notice", provider_name="synthetic provider",
                    expected_404_to_not_found=not_found,
                )
        assert route.call_count == 1
        assert failure.value.details["status"] == status


async def test_provider_keeps_search_parameters_across_retry(app_config, monkeypatch):
    monkeypatch.setattr(HttpAdapter._request_with_retry.retry, "wait", wait_none())
    with respx.mock as router:
        route = router.get(f"{MOCK_API}/search").mock(side_effect=[
            httpx.ReadTimeout("synthetic timeout"),
            httpx.Response(503),
            httpx.Response(200, json={"items": []}),
        ])
        async with HttpAdapter(app_config, base_url=MOCK_API) as adapter:
            body = await adapter.get_json(
                "/search", provider_name="synthetic provider",
                params={"regNumber": "0358300000826000043"},
            )
        assert body == {"items": []}
        assert route.call_count == 3
        assert all(call.request.url.params["regNumber"] == "0358300000826000043" for call in route.calls)


async def test_provider_unusable_body_or_exhausted_retries_are_errors(app_config, monkeypatch):
    monkeypatch.setattr(HttpAdapter._request_with_retry.retry, "wait", wait_none())
    with respx.mock as router:
        bad_json = router.get(f"{MOCK_API}/bad-json").respond(200, text="<html>maintenance</html>")
        unavailable = router.get(f"{MOCK_API}/unavailable").respond(503)
        async with HttpAdapter(app_config) as adapter:
            with pytest.raises(ParseError, match="ответ не JSON"):
                await adapter.get_json(f"{MOCK_API}/bad-json", provider_name="synthetic provider")
            with pytest.raises(ProviderUnavailableError, match="исчерпан retry"):
                await adapter.get_json(f"{MOCK_API}/unavailable", provider_name="synthetic provider")
        assert bad_json.call_count == 1
        assert unavailable.call_count == 3


@pytest.mark.parametrize("transport", ["stdio", "streamable-http", "sse"])
def test_cli_passes_transport_and_only_relevant_bind_options(monkeypatch, transport):
    run = Mock()
    monkeypatch.setattr(server.mcp, "run", run)
    assert server.main(["--transport", transport, "--host", "127.0.0.8", "--port", "8767"]) == 0
    expected = {"transport": transport}
    if transport != "stdio":
        expected.update(host="127.0.0.8", port=8767)
    run.assert_called_once_with(**expected)


def test_cli_config_check_reports_offline_only_without_starting(monkeypatch, capsys):
    monkeypatch.setenv("MCP_ZAKUPKI_PROVIDERS", "damia")
    run = Mock()
    monkeypatch.setattr(server.mcp, "run", run)
    assert server.main(["--check-config"]) == 0
    assert "WARN:" in capsys.readouterr().out
    run.assert_not_called()


def test_cli_config_precheck_does_not_print_provider_key(monkeypatch, capsys):
    monkeypatch.setenv("MCP_ZAKUPKI_ATOMNO_API_KEY", "synthetic-private-key")
    monkeypatch.setenv("MCP_ZAKUPKI_PROVIDERS", "damia")
    assert server.main(["--check-config"]) == 0
    captured = capsys.readouterr()
    assert "atomno_hosted    configured" in captured.out
    assert "synthetic-private-key" not in captured.out + captured.err


async def test_resolver_explicit_provider_does_not_switch_to_another_source(tmp_path):
    config = AppConfig.from_env({
        "MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "resolver.sqlite"),
        "MCP_ZAKUPKI_PROVIDERS": "eis_public,damia",
        "MCP_ZAKUPKI_DAMIA_KEY": "synthetic-key",
    })
    with respx.mock(assert_all_called=False) as router:
        eis = router.get("https://zakupki.gov.ru/epz/order/extendedsearch/results.html").respond(
            200, text='<p class="noRecords">Поиск не дал результатов</p>',
        )
        damia = router.get("https://api.damia.ru/zakupki/zsearch").respond(500)
        async with ProviderResolver(config) as resolver:
            with pytest.raises(ProviderUnavailableError):
                await resolver.search_tenders(TenderSearchFilter(query="paper"), provider_name="damia")
            empty = await resolver.search_tenders(TenderSearchFilter(query="paper"), provider_name="eis_public")
        assert empty.search_meta.source == "eis_public"
        assert empty.tenders == []
        assert damia.call_count == eis.call_count == 1


async def test_byok_limit_blocks_new_card_without_blocking_a_cache_hit(tmp_path):
    config = AppConfig.from_env({
        "MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "byok.sqlite"),
        "MCP_ZAKUPKI_PROVIDERS": "damia",
        "MCP_ZAKUPKI_DAMIA_KEY": "synthetic-key",
        "MCP_ZAKUPKI_BYOK_DAILY_LIMIT": "1",
    })
    number = "0358300000826000043"
    with respx.mock as router:
        route = router.get("https://api.damia.ru/zakupki/zakupka").respond(200, json={
            "reg": number, "title": "Синтетическое извещение", "fz": "44", "price": 123.45,
        })
        async with ServiceContext(config) as ctx:
            assert (await get_tender(ctx, reg_number=number)).source_provider == "damia"
            assert (await get_tender(ctx, reg_number=number)).reg_number == number
            with pytest.raises(RateLimitedError) as failure:
                await get_tender(ctx, reg_number="0358300000826000044")
        assert route.call_count == 1
        assert failure.value.details["limit"] == 1


@pytest.fixture
async def hosted_mcp(tmp_path, monkeypatch):
    config = AppConfig.from_env({
        "MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "hosted-tools.sqlite"),
        "MCP_ZAKUPKI_API_KEY": "synthetic-key",
    })
    async with ServiceContext(config) as ctx:
        monkeypatch.setattr(server, "_ctx", ctx)
        async with Client(server.mcp) as client:
            yield client


@pytest.mark.parametrize("role", ["supplier", "customer"])
async def test_mcp_hosted_organization_success_and_cache(hosted_mcp, role):
    tool = "get_supplier_stats" if role == "supplier" else "get_customer_history"
    endpoint = "supplier-stats" if role == "supplier" else "customer-history"
    with respx.mock as router:
        route = router.post(f"{HOSTED_API}/{endpoint}").respond(200, json={
            "org_inn": "7707083893",
            "org_role": role,
            "short_name": "Синтетическая организация",
            "period_from": "2026-01-01",
            "period_to": "2026-06-01",
            "coverage": "identity",
            "fetched_at": "2026-11-22T10:00:00Z",
            "source_provider": "synthetic",
        })
        args = {"inn": "7707083893", "period_from": "2026-01-01", "period_to": "2026-06-01"}
        first = await hosted_mcp.call_tool(tool, args)
        cached = await hosted_mcp.call_tool(tool, args)
    assert first.data == cached.data
    assert first.data["org_role"] == role
    assert first.data["tenders_total"] is None
    assert route.call_count == 1
    assert route.calls[0].request.headers["x-api-key"] == "synthetic-key"
    assert json.loads(route.calls[0].request.content) == {**args, "ogrn": None}


async def test_mcp_reports_invalid_filters_and_unanswered_hosted_search(hosted_mcp):
    with respx.mock as router:
        route = router.post(f"{HOSTED_API}/search").mock(side_effect=httpx.ConnectError("offline"))
        invalid = await hosted_mcp.call_tool("search_tenders", {"limit": 11})
        unavailable = await hosted_mcp.call_tool("search_tenders", {"query": "32616599714"})
    assert invalid.data["error"] == "invalid_input"
    assert invalid.data["details"]["allowed"] == [10, 20, 50]
    assert unavailable.data["error"] == "hosted_unavailable"
    assert unavailable.data["details"]["reason"] == "ConnectError"
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (200, {"status": "closed_beta", "message_ru": "Закрытая проверка"}, "hosted_unavailable"),
        (200, {"error": "not_implemented", "message_ru": "Сводка недоступна"}, "hosted_unavailable"),
        (502, "<html>upstream error</html>", "hosted_unavailable"),
        (200, "<html>not JSON</html>", "parse_error"),
    ],
)
async def test_mcp_reports_api_failures_as_errors(hosted_mcp, status, body, error):
    with respx.mock as router:
        response = {"json": body} if isinstance(body, dict) else {"text": body}
        router.post(f"{HOSTED_API}/supplier-stats").respond(status, **response)
        result = await hosted_mcp.call_tool("get_supplier_stats", {"inn": "7707083893"})
    assert result.data["error"] == error
    assert result.data.get("tenders_total") is None


@pytest.mark.parametrize("tool", ["get_customer_history", "get_supplier_stats", "get_tender", "lookup_okpd2"])
async def test_mcp_returns_structured_validation_error_without_network(hosted_mcp, tool):
    args = {
        "get_customer_history": {},
        "get_supplier_stats": {},
        "get_tender": {"reg_number": "326165997140"},
        "lookup_okpd2": {"query": "a"},
    }
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__startswith=HOSTED_API).respond(500)
        result = await hosted_mcp.call_tool(tool, args[tool])
    assert result.data["error"] == "invalid_input"
    assert route.call_count == 0
