"""Wire-level tests for the optional procurement providers, with no live credentials."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
import respx
from httpx import Response

from mcp_zakupki.config import AppConfig
from mcp_zakupki.errors import (
    AuthFailedError,
    NotFoundError,
    ParseError,
    ProviderUnavailableError,
    RateLimitedError,
)
from mcp_zakupki.providers.base import ProviderCapability
from mcp_zakupki.providers.damia import DamiaProvider
from mcp_zakupki.providers.gosplan import GosplanProvider
from mcp_zakupki.providers.http_client import HttpAdapter
from mcp_zakupki.providers.navodki import NavodkiProvider
from mcp_zakupki.schemas import LawType, Platform, TenderSearchFilter, TenderStatus

REG_44 = "0358300000826000043"
REG_223 = "32616599714"
INN = "6154058393"
OGRN = "1026102580754"
GOSPLAN = "https://gosplan.example/v2/notifications"
NAVODKI = "https://navodki.ru/api/v1/tenders"
DAMIA = "https://api.damia.ru/zakupki"
EIS = "https://zakupki.gov.ru/epz/order/notice/printForm"

PROVIDERS = (
    pytest.param(GosplanProvider, GOSPLAN, f"{GOSPLAN}/{REG_44}", id="gosplan"),
    pytest.param(NavodkiProvider, f"{NAVODKI}/search", f"{NAVODKI}/{REG_44}", id="navodki"),
    pytest.param(DamiaProvider, f"{DAMIA}/zsearch", f"{DAMIA}/zakupka", id="damia"),
)


@pytest.fixture
def config(tmp_path):
    return AppConfig.from_env(
        {
            "MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "alternate.sqlite"),
            "MCP_ZAKUPKI_GOSPLAN_BASE": "https://gosplan.example/",
            "MCP_ZAKUPKI_GOSPLAN_KEY": "mock-gosplan",
            "MCP_ZAKUPKI_NAVODKI_KEY": "mock-navodki",
            "MCP_ZAKUPKI_DAMIA_KEY": "mock-damia",
        }
    )


@pytest.fixture
def filters():
    return TenderSearchFilter(
        query="офисная бумага",
        law_type=[LawType.FZ_44, LawType.FZ_223, LawType.PP_615],
        okpd2_codes=["17.12", "17.23"],
        regions=["61", "77"],
        customer_inn=INN,
        price_min_rub=Decimal("0"),
        price_max_rub=Decimal("150000.50"),
        smp_only=True,
        publish_date_from="2026-11-01",
        publish_date_to="2026-11-30",
        limit=10,
        next_page_token="3",
    )


@pytest.mark.parametrize(("provider", "search_url", "card_url"), PROVIDERS)
async def test_alternate_provider_requires_explicit_credentials(
    provider, search_url, card_url, app_config
):
    with respx.mock() as router:
        async with provider(app_config) as adapter:
            assert not adapter.is_configured
            assert adapter.info.requires_auth
            assert adapter.info.capabilities & ProviderCapability.TENDER_DETAILS
            with pytest.raises(AuthFailedError) as error:
                await adapter.get_tender(REG_44)
        assert error.value.details["provider"] == adapter.name
        assert not router.calls


@pytest.mark.parametrize(("provider", "search_url", "card_url"), PROVIDERS)
async def test_alternate_provider_distinguishes_permissions_and_absent_card(
    provider, search_url, card_url, config
):
    with respx.mock() as router:
        search = router.get(search_url).mock(side_effect=[Response(401), Response(429)])
        missing = router.get(card_url).respond(404)
        async with provider(config) as adapter:
            assert adapter.is_configured
            with pytest.raises(AuthFailedError) as denied:
                await adapter.search_tenders(TenderSearchFilter())
            assert denied.value.details["status"] == 401
            with pytest.raises(RateLimitedError) as limited:
                await adapter.search_tenders(TenderSearchFilter())
            assert limited.value.details["status"] == 429
            with pytest.raises(NotFoundError) as not_found:
                await adapter.get_tender(REG_44)
            assert not_found.value.details["status"] == 404
        assert search.call_count == 2
        assert missing.called


@pytest.mark.parametrize(("provider", "search_url", "card_url"), PROVIDERS[:2])
async def test_alternate_provider_handles_body_error_and_empty_card(
    provider, search_url, card_url, config
):
    with respx.mock() as router:
        query = router.get(search_url).mock(
            side_effect=[
                Response(200, json={"error": "tariff unavailable"}),
                Response(200, json={"Ошибка": "Источник недоступен."}),
                Response(200, json=["unexpected envelope"]),
                Response(200, json={"items": None}),
            ]
        )
        card = router.get(card_url).mock(
            side_effect=[
                Response(200, json={}),
                Response(200, json=["unexpected envelope"]),
            ]
        )
        async with provider(config) as adapter:
            for message in ("tariff unavailable", "Источник недоступен."):
                with pytest.raises(ProviderUnavailableError) as rejected:
                    await adapter.search_tenders(TenderSearchFilter(law_type=[]))
                assert rejected.value.details["upstream"] == message
            with pytest.raises(ParseError):
                await adapter.search_tenders(TenderSearchFilter(law_type=[]))
            empty = await adapter.search_tenders(TenderSearchFilter(law_type=[]))
            assert empty.tenders == []
            assert empty.total_estimated is None
            assert empty.page_size == 0
            for _ in range(2):
                with pytest.raises(NotFoundError) as absent:
                    await adapter.get_tender(REG_44)
                assert absent.value.details["reg_number"] == REG_44
        assert query.call_count == 4
        assert card.call_count == 2


async def test_gosplan_search_uses_filter_params_and_reads_data_alias(config, filters):
    upstream = {
        "data": [
            {
                "reg": REG_44,
                "title": "Бумага",
                "fz": "44",
                "okpd": "17.12, , 17.23",
                "customer_inn": INN,
                "customer_name": "Городская больница",
                "region": 61,
                "smp": True,
            },
            {
                "reg_number": REG_223,
                "name": "Канцтовары",
                "law": "223-ФЗ",
                "okpd": [17, "17.23"],
                "region": "",
                "url": "https://zakupki.gov.ru/notice",
            },
            {"id": "615-sample", "law": "615-ПП", "okpd": None, "region": None, "price": 39000},
            None,
        ],
        "total": "36",
    }
    with respx.mock() as router:
        route = router.get(GOSPLAN).respond(200, json=upstream)
        async with GosplanProvider(config) as adapter:
            result = await adapter.search_tenders(filters)
        request = route.calls.last.request
    assert request.headers["X-API-Key"] == "mock-gosplan"
    params = dict(request.url.params)
    assert Decimal(params.pop("price_min")) == 0
    assert Decimal(params.pop("price_max")) == Decimal("150000.50")
    assert params == {
        "limit": "10",
        "q": "офисная бумага",
        "okpd": "17.12,17.23",
        "region": "61,77",
        "customer_inn": INN,
        "smp": "1",
        "publish_from": "2026-11-01",
        "publish_to": "2026-11-30",
        "fz": "44,223,615",
        "page": "3",
    }
    assert result.query_echo == filters.model_dump(mode="json")
    assert result.total_estimated == 36
    assert result.page_size == 3
    assert result.next_page_token is None
    assert result.search_meta.source == "gosplan"
    first, second, third = result.tenders
    assert (first.reg_number, first.law_type, first.okpd2_codes) == (
        REG_44,
        LawType.FZ_44,
        ["17.12", "17.23"],
    )
    assert (first.customer.inn, first.customer.region_code, first.smp_only) == (INN, "61", True)
    assert first.url_eis == f"{EIS}/view.html?regNumber={REG_44}"
    assert (second.reg_number, second.law_type, second.okpd2_codes) == (
        REG_223,
        LawType.FZ_223,
        ["17", "17.23"],
    )
    assert second.customer.region_code is None
    assert second.url_eis == upstream["data"][1]["url"]
    assert third.reg_number == "615-sample"
    assert third.law_type == LawType.PP_615
    assert third.title == "—"
    assert third.price_initial_rub is None
    assert third.status == TenderStatus.UNKNOWN


@pytest.mark.parametrize(
    "customer",
    [
        {"inn": INN, "ogrn": OGRN, "name": "Заказчик", "region": 61, "address": "Таганрог"},
        None,
    ],
    ids=["nested-customer", "null-customer"],
)
async def test_gosplan_card_maps_nested_customer_without_inventing_details(config, customer):
    url = f"{GOSPLAN}/{REG_44}"
    with respx.mock() as router:
        route = router.get(url).respond(
            200,
            json={
                "name": "Закупка в учреждении",
                "okpd": ["17.12"],
                "customer": customer,
                "smp": False,
            },
        )
        async with GosplanProvider(config) as adapter:
            card = await adapter.get_tender(REG_44)
        assert route.calls.last.request.headers["X-API-Key"] == "mock-gosplan"
    assert card.reg_number == REG_44
    assert card.title == "Закупка в учреждении"
    assert card.okpd2_codes == ["17.12"]
    assert card.url_xml == f"{EIS}/viewXml.html?regNumber={REG_44}"
    assert card.customer.inn == (INN if customer else "")
    assert card.customer.ogrn == (OGRN if customer else None)
    assert card.customer.short_name == ("Заказчик" if customer else "—")
    assert card.customer.address == ("Таганрог" if customer else None)
    assert card.price_initial_rub is None
    assert card.publish_date is None
    assert card.applications_deadline is None


async def test_gosplan_unparseable_total_keeps_results(config):
    with respx.mock() as router:
        router.get(GOSPLAN).respond(200, json={"total": "UNKNOWN", "items": [{"id": REG_44}]})
        async with GosplanProvider(config) as adapter:
            result = await adapter.search_tenders(TenderSearchFilter())
    assert [row.reg_number for row in result.tenders] == [REG_44]
    assert result.total_estimated is None


async def test_navodki_search_sends_bearer_and_maps_flat_items(config, filters):
    with respx.mock() as router:
        route = router.get(f"{NAVODKI}/search").respond(
            200,
            json={
                "items": [
                    {
                        "reg": REG_223,
                        "title": "Канцтовары",
                        "fz": "223",
                        "okpd": "17.12, 17.23",
                        "customer_inn": INN,
                        "customer_name": "Колледж",
                        "region": 54,
                        "smp": True,
                    },
                    {
                        "reg_number": REG_44,
                        "name": "Архив",
                        "law": "615-ПП",
                        "okpd": [17, "17.23"],
                        "region": "",
                        "price": "29.00",
                        "url": "https://zakupki.gov.ru/notice",
                    },
                    "ignored separator",
                    {"id": 12},
                ],
                "total": 14,
            },
        )
        async with NavodkiProvider(config) as adapter:
            result = await adapter.search_tenders(filters)
        request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer mock-navodki"
    params = dict(request.url.params)
    assert Decimal(params.pop("price_min")) == 0
    assert Decimal(params.pop("price_max")) == Decimal("150000.50")
    assert params == {
        "limit": "10",
        "q": "офисная бумага",
        "okpd": "17.12,17.23",
        "region": "61,77",
        "customer": INN,
        "smp": "1",
    }
    assert result.page_size == 3
    assert result.total_estimated == 14
    assert result.next_page_token is None
    assert result.search_meta.source == "navodki"
    first, second, third = result.tenders
    assert (first.reg_number, first.law_type) == (REG_223, LawType.FZ_223)
    assert (first.customer.inn, first.customer.region_code, first.smp_only) == (INN, "54", True)
    assert first.okpd2_codes == ["17.12", "17.23"]
    assert (second.reg_number, second.law_type, second.customer.region_code) == (
        REG_44,
        LawType.PP_615,
        None,
    )
    assert second.okpd2_codes == ["17", "17.23"]
    assert second.price_initial_rub is None
    assert second.url_eis == "https://zakupki.gov.ru/notice"
    assert (third.reg_number, third.law_type, third.title, third.okpd2_codes) == (
        "12",
        LawType.FZ_44,
        "—",
        [],
    )


async def test_navodki_data_alias_and_card_use_requested_number(config):
    with respx.mock() as router:
        router.get(f"{NAVODKI}/search").respond(200, json={"data": [{"reg": REG_44}]})
        card_route = router.get(f"{NAVODKI}/{REG_44}").respond(
            200,
            json={
                "reg": "not-the-requested-number",
                "name": "Бумага",
                "customer_inn": None,
                "region": None,
            },
        )
        async with NavodkiProvider(config) as adapter:
            result = await adapter.search_tenders(TenderSearchFilter())
            card = await adapter.get_tender(REG_44)
        assert card_route.calls.last.request.headers["Authorization"] == "Bearer mock-navodki"
    assert [row.reg_number for row in result.tenders] == [REG_44]
    assert card.reg_number == REG_44
    assert card.title == "Бумага"
    assert card.customer.inn == ""
    assert card.customer.region_code is None
    assert card.documents == []


async def test_damia_search_sends_key_and_filters_and_handles_variants(config, filters):
    filters = filters.model_copy(update={"platform": [Platform.RTS_TENDER, Platform.ROSELTORG]})
    rows = [
        {
            "reg": REG_44,
            "title": "Бумага",
            "fz": "44-ФЗ",
            "okpd": "17.12, ,17.23",
            "customer_inn": INN,
            "customer_name": "Больница",
            "region": 61,
            "price": "17397.00",
            "dtPublish": "2026-11-21T09:23:32Z",
            "dtEnd": "2026-11-30T09:00:00+03:00",
            "status": "active",
            "smp": True,
            "url": "https://zakupki.gov.ru/notice",
        },
        {
            "regNumber": REG_223,
            "name": "Канцтовары",
            "law": "223 ФЗ",
            "okpd": [17, "17.23"],
            "zakazInn": INN,
            "zakazName": "Колледж",
            "regionCode": "54",
            "status": "applications_closed",
            "price": "не указана",
            "publish_date": "неизвестно",
        },
        {"reestrNumber": "615-sample", "law": "615-ПП", "status": "finished"},
        {"reg": "cancelled", "status": "cancelled"},
        {"reg": "published", "status": "published"},
        {"reg": "unknown", "status": "unmapped"},
        {"reg": "null", "status": None},
        None,
    ]
    with respx.mock() as router:
        route = router.get(f"{DAMIA}/zsearch").respond(200, json={"zakupki": rows, "total": "41"})
        async with DamiaProvider(config) as adapter:
            result = await adapter.search_tenders(filters)
        request = route.calls.last.request
    params = dict(request.url.params)
    assert Decimal(params.pop("pricemin")) == 0
    assert Decimal(params.pop("pricemax")) == Decimal("150000.50")
    assert params == {
        "key": "mock-damia",
        "limit": "10",
        "q": "офисная бумага",
        "okpd": "17.12,17.23",
        "region": "61,77",
        "customer_inn": INN,
        "etp": "rts_tender,roseltorg",
        "smp": "1",
        "from": "2026-11-01",
        "to": "2026-11-30",
        "fz": "44,223,615",
        "page": "3",
    }
    assert result.query_echo == filters.model_dump(mode="json")
    assert result.total_estimated == 41
    assert result.page_size == 7
    assert result.next_page_token is None
    assert result.search_meta.source == "damia"
    first, second, third, *_ = result.tenders
    assert [tender.status for tender in result.tenders] == [
        TenderStatus.APPLICATIONS_OPEN,
        TenderStatus.APPLICATIONS_CLOSED,
        TenderStatus.COMPLETED,
        TenderStatus.CANCELLED,
        TenderStatus.PUBLISHED,
        TenderStatus.UNKNOWN,
        TenderStatus.UNKNOWN,
    ]
    assert (first.reg_number, first.law_type, first.price_initial_rub) == (
        REG_44,
        LawType.FZ_44,
        Decimal("17397.00"),
    )
    assert first.publish_date == datetime.fromisoformat("2026-11-21T09:23:32+00:00")
    assert first.applications_deadline == datetime.fromisoformat("2026-11-30T09:00:00+03:00")
    assert first.okpd2_codes == ["17.12", "17.23"]
    assert first.url_eis == rows[0]["url"]
    assert (second.reg_number, second.law_type, second.customer.region_code) == (
        REG_223,
        LawType.FZ_223,
        "54",
    )
    assert second.customer.inn == INN
    assert second.okpd2_codes == ["17", "17.23"]
    assert second.price_initial_rub is None
    assert second.publish_date is None
    assert (third.law_type, third.customer.region_code, third.okpd2_codes) == (
        LawType.PP_615,
        None,
        [],
    )


async def test_damia_search_accepts_bare_result_list_without_an_estimated_total(config):
    rows = [
        {"reg": REG_44, "fz": "44", "title": "Синтетическое извещение 44-ФЗ"},
        {"reg": REG_223, "fz": "223", "title": "Синтетическое извещение 223-ФЗ"},
    ]
    with respx.mock() as router:
        route = router.get(f"{DAMIA}/zsearch").respond(200, json=rows)
        async with DamiaProvider(config) as adapter:
            result = await adapter.search_tenders(TenderSearchFilter(query="бумага"))
    assert route.call_count == 1
    assert [tender.reg_number for tender in result.tenders] == [REG_44, REG_223]
    assert [tender.law_type for tender in result.tenders] == [LawType.FZ_44, LawType.FZ_223]
    assert result.page_size == 2
    assert result.total_estimated is None


@pytest.mark.parametrize(
    ("body", "upstream"),
    [
        ("Ошибка: Неверный ключ (1)", "Ошибка: Неверный ключ (1)"),
        ("", "пустой ответ источника"),
        ({"error": "invalid subscription"}, "invalid subscription"),
        ({"Ошибка": "Недоступно"}, "Недоступно"),
        (None, "пустой ответ источника"),
    ],
)
async def test_damia_rejects_refusal_in_successful_http_response(config, body, upstream):
    response = Response(200, content=b"null") if body is None else Response(200, json=body)
    with respx.mock() as router:
        router.get(f"{DAMIA}/zsearch").mock(return_value=response)
        async with DamiaProvider(config) as adapter:
            with pytest.raises(ProviderUnavailableError) as rejected:
                await adapter.search_tenders(TenderSearchFilter(query="бумага"))
    assert rejected.value.details["provider"] == "damia"
    assert rejected.value.details["upstream"] == upstream


@pytest.mark.parametrize("payload", [True, {"items": 42}])
async def test_damia_invalid_search_shape_is_parse_error(config, payload):
    with respx.mock() as router:
        router.get(f"{DAMIA}/zsearch").respond(200, json=payload)
        async with DamiaProvider(config) as adapter:
            with pytest.raises(ParseError, match="/zsearch"):
                await adapter.search_tenders(TenderSearchFilter())


async def test_damia_card_maps_documents_and_nullable_fields(config):
    with respx.mock() as router:
        route = router.get(f"{DAMIA}/zakupka").mock(
            side_effect=[
                Response(
                    200,
                    json={
                        "name": "Бумага",
                        "title_full": "Поставка офисной бумаги",
                        "fz": "44",
                        "okpd": ["17.12"],
                        "customer": {
                            "inn": INN,
                            "ogrn": OGRN,
                            "name": "Больница",
                            "full_name": "Городская больница",
                            "region": 61,
                            "address": "Таганрог",
                        },
                        "priceInitial": "17397",
                        "publish_date": "2026-11-21T12:23:32+03:00",
                        "end_date": "2026-11-30T09:00:00+03:00",
                        "status": "open",
                        "documents": [
                            {"name": "Техническое задание", "url": "https://zakupki.gov.ru/doc/1"},
                            {"title": "Проект договора", "url": "https://zakupki.gov.ru/doc/2"},
                            {"name": "Ссылка отсутствует"},
                            None,
                        ],
                        "url": "https://zakupki.gov.ru/notice",
                    },
                ),
                Response(
                    200,
                    json={"customer": None, "customer_inn": INN, "documents": None, "price": "n/a"},
                ),
            ]
        )
        async with DamiaProvider(config) as adapter:
            card = await adapter.get_tender(REG_44)
            sparse = await adapter.get_tender(REG_223)
    assert [dict(call.request.url.params) for call in route.calls] == [
        {"reg": REG_44, "key": "mock-damia"},
        {"reg": REG_223, "key": "mock-damia"},
    ]
    assert (card.title, card.title_full, card.reg_number) == (
        "Бумага",
        "Поставка офисной бумаги",
        REG_44,
    )
    assert (card.customer.inn, card.customer.ogrn, card.customer.region_code) == (INN, OGRN, "61")
    assert card.customer.full_name == "Городская больница"
    assert card.price_initial_rub == Decimal("17397")
    assert card.applications_deadline == datetime.fromisoformat("2026-11-30T09:00:00+03:00")
    assert [document.title for document in card.documents] == [
        "Техническое задание",
        "Проект договора",
    ]
    assert card.url_eis == "https://zakupki.gov.ru/notice"
    assert sparse.customer.inn == INN
    assert sparse.customer.region_code is None
    assert sparse.title_full is None
    assert sparse.documents == []
    assert sparse.price_initial_rub is None


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({}, NotFoundError),
        ({"error": "absent"}, NotFoundError),
        (["wrong envelope"], ParseError),
        ({"documents": 12}, ParseError),
    ],
)
async def test_damia_card_distinguishes_missing_and_invalid_body(config, body, error):
    with respx.mock() as router:
        router.get(f"{DAMIA}/zakupka").respond(200, json=body)
        async with DamiaProvider(config) as adapter:
            with pytest.raises(error) as failure:
                await adapter.get_tender(REG_44)
    assert failure.value.details["reg_number"] == REG_44


async def test_damia_customer_and_supplier_map_totals_and_use_different_query_keys(config):
    with respx.mock() as router:
        customers = router.get(f"{DAMIA}/customer").respond(
            200,
            json={
                "name": "Больница",
                "region": 61,
                "tenders_total": "12",
                "tenders_completed": "9",
                "tenders_cancelled": "1",
                "sum_price": "9000.50",
                "avg_price": "1000.05",
                "median_price": "950",
                "smp_share_pct": "30.5",
                "rnp": "clean",
            },
        )
        suppliers = router.get(f"{DAMIA}/contracts").respond(
            200,
            json={
                "count": "bad-count",
                "volume": "500.00",
                "avg_price": None,
                "smp_share_pct": "unknown",
                "rnp": True,
            },
        )
        async with DamiaProvider(config) as adapter:
            customer = await adapter.get_customer_history(inn=INN, period_from="2026-01-01")
            supplier = await adapter.get_supplier_stats(ogrn=OGRN, period_to="2026-11-30")
    assert dict(customers.calls.last.request.url.params) == {
        "req": INN,
        "key": "mock-damia",
        "from": "2026-01-01",
    }
    assert dict(suppliers.calls.last.request.url.params) == {
        "inn": OGRN,
        "type": "supplier",
        "key": "mock-damia",
        "to": "2026-11-30",
    }
    assert (customer.org_inn, customer.org_role, customer.region_code) == (INN, "customer", "61")
    assert (customer.period_from, customer.period_to) == ("2026-01-01", "")
    assert (customer.tenders_total, customer.tenders_completed, customer.tenders_cancelled) == (
        12,
        9,
        1,
    )
    assert (customer.total_volume_rub, customer.avg_contract_rub, customer.median_contract_rub) == (
        Decimal("9000.50"),
        Decimal("1000.05"),
        Decimal("950"),
    )
    assert (customer.smp_share_pct, customer.rnp_status) == (30.5, "clean")
    assert (supplier.org_inn, supplier.org_role, supplier.period_from) == (OGRN, "supplier", "")
    assert (supplier.tenders_total, supplier.total_volume_rub, supplier.smp_share_pct) == (
        0,
        Decimal("500.00"),
        None,
    )
    assert (supplier.rnp_status, supplier.short_name, supplier.region_code) == (
        "in_rnp",
        None,
        None,
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get_customer_history", "customer"),
        ("get_supplier_stats", "contracts"),
    ],
)
async def test_damia_analytics_checks_identifier_and_corrupt_response(config, method, path):
    with respx.mock() as router:
        route = router.get(f"{DAMIA}/{path}").mock(
            side_effect=[
                Response(200, json=["invalid aggregate"]),
                Response(200, json={"rnp": "unrecognized"}),
            ]
        )
        async with DamiaProvider(config) as adapter:
            read = getattr(adapter, method)
            with pytest.raises(AuthFailedError, match="ИНН"):
                await read()
            assert not route.called
            with pytest.raises(ParseError, match=path):
                await read(inn=INN, period_from="2026-01-01", period_to="2026-11-30")
            result = await read(inn=INN)
    assert result.rnp_status == "unknown"
    assert result.total_volume_rub is None


@pytest.mark.parametrize(("provider", "search_url", "card_url"), PROVIDERS)
async def test_alternate_provider_can_use_http_owned_by_resolver(
    provider, search_url, card_url, config
):
    class SharedHttp(HttpAdapter):
        async def aclose(self):
            pytest.fail("The provider closed an HTTP client it does not own")

    with respx.mock() as router:
        router.get(search_url).respond(200, json={"items": []})
        http = SharedHttp(config)
        try:
            async with provider(config, http=http) as adapter:
                result = await adapter.search_tenders(TenderSearchFilter())
                assert result.tenders == []
            assert (await http.get_json(search_url, provider_name=adapter.name)) == {"items": []}
        finally:
            await HttpAdapter.aclose(http)
