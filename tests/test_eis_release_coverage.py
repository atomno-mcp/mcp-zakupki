"""Регрессии разбора извещений, страницы ЕИС и прежнего XML-адаптера."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mcp_zakupki.config import AppConfig
from mcp_zakupki.errors import NotFoundError, ParseError, ProviderUnavailableError, ValidationError
from mcp_zakupki.providers.eis_notice_xml import parse_notice_xml, print_links_223, source_datetime
from mcp_zakupki.providers.eis_public import (
    EisPublicProvider,
    parse_org_html,
    parse_rnp_status,
    parse_search_html,
)
from mcp_zakupki.providers.eis_regions import customer_region, normalize_customer_regions
from mcp_zakupki.providers.html_fallback import HtmlFallbackProvider
from mcp_zakupki.providers.http_client import HttpAdapter
from mcp_zakupki.schemas import FileType, LawType, Platform, TenderSearchFilter, TenderStatus

NO_44 = "0358300000826000043"
NO_223 = "32616599714"
GUID = "441f1990-628e-43c4-ac47-3c3f7808ac4e"
HOST = "https://zakupki.gov.ru"
SEARCH = f"{HOST}/epz/order/extendedsearch/results.html"
XML_44 = f"{HOST}/epz/order/notice/printForm/viewXml.html"
PRINT_223 = (
    "/epz/order/notice/notice223/printForm/view.html"
    f"?purchaseNoticeGuid={GUID}&purchaseNoticeNumber={NO_223}"
)
XML_223 = f"{HOST}/epz/order/notice/notice223/printForm/viewXml.html"
EMPTY = '<div class="search-results__total">0 записей</div><div class="noRecords"></div>'
ORG_URL = f"{HOST}/epz/organization/search/results.html"


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig.from_env({"MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "unused.sqlite")})


def _row(
    number: str = NO_44,
    *,
    law: str = "44-ФЗ",
    status: str = "Подача заявок",
    print_link: str = "",
    card_path: str = "/epz/order/notice/ea20/view/common-info.html",
) -> str:
    html_print_link = f'<a href="{escape(print_link)}">Печать</a>' if print_link else ""
    return f"""
    <div class="registry-entry__form">
      <div class="registry-entry__header-top__title">{law}</div>
      <div class="registry-entry__header-top__icon">{html_print_link}</div>
      <div class="registry-entry__header-mid__number">
        <a href="{card_path}?regNumber={number}">№ {number}</a>
      </div>
      <div class="registry-entry__header-mid__title">{status}</div>
      <div class="registry-entry__body-block">
        <div class="registry-entry__body-title">Объект закупки</div>
        <div class="registry-entry__body-value">Канцелярские товары</div>
      </div>
      <div class="registry-entry__body-block">
        <div class="registry-entry__body-title">Организация, осуществляющая размещение</div>
        <div class="registry-entry__body-href">Организатор</div>
      </div>
      <div class="registry-entry__body-block">
        <div class="registry-entry__body-title">Заказчик</div>
        <div class="registry-entry__body-href">
          <a href="/epz/organization/view223/info.html?inn=5403106778&amp;ogrn=1025401307800">Заказчик</a>
        </div>
      </div>
      <div class="price-block__value">17&nbsp;397,50 ₽</div>
      <div class="data-block__title">Размещено</div>
      <div class="data-block__value">21.11.2026</div>
      <div class="data-block__title">Окончание подачи заявок:</div>
      <div class="data-block__value">30.11.2026 09:00</div>
    </div>
    """


def _regional_page(content: str, *, selection: str = "50000000000", page: int = 1) -> str:
    return content + f"""
    <input name="customerPlace" value="{selection}">
    <input name="customerPlaceCodes" value="{selection}">
    <input id="pageNumber" value="{page}">
    <div class="search-results__total">более 1&nbsp;500 записей</div>
    <a class="paginator-button-next" data-pagenumber="{page + 1}"></a>
    """


def _xml44(
    *,
    number: str = NO_44,
    version: str = "1",
    role: str = "CU",
    customer_code: str = "copied-org-code",
    currency: str = "RUB",
    subject: str = "Товары",
    published: str = "2026-11-21T12:23:32+03:00",
    deadline: str = "2026-11-30T09:00:00+03:00",
) -> str:
    return f"""
    <epNotificationEF2020 xmlns="urn:eis" xmlns:o="urn:org">
      <versionNumber>{version}</versionNumber>
      <commonInfo>
        <purchaseNumber>{number}</purchaseNumber>
        <purchaseObjectInfo>{subject}</purchaseObjectInfo>
        <publishDTInEIS>{published}</publishDTInEIS>
        <href>/epz/order/notice/ea20/view/common-info.html?regNumber={number}</href>
        <ETP><code>ETP_RTS</code></ETP>
      </commonInfo>
      <purchaseResponsibleInfo>
        <responsibleRole>{role}</responsibleRole>
        <responsibleOrgInfo>
          <o:regNum>copied-org-code</o:regNum>
          <o:shortName>Ответственный</o:shortName>
          <o:factAddress>129110, Г.Москва, ул. Щепкина</o:factAddress>
          <o:INN>7701000012</o:INN>
        </responsibleOrgInfo>
      </purchaseResponsibleInfo>
      <notificationInfo>
        <contractExecutionPaymentPlan><contractExecutionTermsInfo>
          <notRelativeTermsInfo><endDate>2028-02-14+03:00</endDate></notRelativeTermsInfo>
        </contractExecutionTermsInfo></contractExecutionPaymentPlan>
        <procedureInfo><collectingInfo><endDT>{deadline}</endDT></collectingInfo>
          <biddingDT>2026-11-30T11:00:00+03:00</biddingDT>
        </procedureInfo>
        <contractConditionsInfo><maxPriceInfo>
          <maxPrice>17397.50</maxPrice><currency><code>{currency}</code></currency>
        </maxPriceInfo></contractConditionsInfo>
        <customerRequirementsInfo><customerRequirementInfo><customer>
          <o:regNum>{customer_code}</o:regNum>
          <o:fullName>Учреждение заказчика</o:fullName>
          <o:legalAddress>Московская область, г.о. Воскресенск</o:legalAddress>
          <o:OGRN>1230000000000</o:OGRN>
        </customer></customerRequirementInfo></customerRequirementsInfo>
        <purchaseObjectsInfo><notDrugPurchaseObjectsInfo>
          <purchaseObject><KTRU><code>22.29.25.000-00000001</code></KTRU>
            <OKPD2><code>22.29.25.000</code><name>Принадлежности</name></OKPD2>
          </purchaseObject>
          <purchaseObject><OKPD2><code>22.29.25.000</code><name>Повтор</name></OKPD2></purchaseObject>
          <purchaseObject><OKPD2><name>Без кода</name></OKPD2></purchaseObject>
        </notDrugPurchaseObjectsInfo></purchaseObjectsInfo>
      </notificationInfo>
      <attachmentsInfo>
        <attachmentInfo><fileName>Условия.DOCX</fileName><url>/44fz/file?a=1</url><fileSize>42</fileSize></attachmentInfo>
        <attachmentInfo><fileName>Дубликат.docx</fileName><url>/44fz/file?a=1</url></attachmentInfo>
        <attachmentInfo><fileName>Другая площадка</fileName><url>https://other.example/file</url></attachmentInfo>
        <attachmentInfo><docDescription>Смета.xlsx</docDescription><downloadUrl>/44fz/file?a=2</downloadUrl><fileSize>unknown</fileSize></attachmentInfo>
        <attachmentInfo><url>/44fz/file?a=3</url></attachmentInfo>
      </attachmentsInfo>
    </epNotificationEF2020>
    """


def _xml223(
    *,
    number: str = NO_223,
    subject: str = "Товары для колледжа",
    customer_offset: str = "4",
    placer_offset: str = "4",
    deadline: str = "2026-11-20T11:15:00",
    lots: tuple[tuple[str, str], ...] = (("300827.12", "RUB"), ("15.03", "RUB")),
) -> str:
    lot_rows = "".join(
        f"<lot><lotData><initialSum>{amount}</initialSum><currency><code>{currency}</code></currency>"
        "<lotItems><lotItem><okpd2><code>22.29.25.000</code><name>Принадлежности</name></okpd2>"
        "</lotItem></lotItems></lotData></lot>"
        for amount, currency in lots
    )
    return f"""
    <purchaseNoticeZKESMBO xmlns="urn:notice">
      <body><item><purchaseNoticeZKESMBOData>
        <registrationNumber>{number}</registrationNumber>
        <name>{subject}</name>
        <customer><mainInfo><shortName>Колледж</shortName>
          <inn>5403106778</inn><region>Новосибирская область</region>
          <timeZone><offset>{customer_offset}</offset></timeZone>
        </mainInfo></customer>
        <placer><mainInfo><timeZone><offset>{placer_offset}</offset></timeZone></mainInfo></placer>
        <status>P</status><version>1</version>
        <publicationDateTime>2026-11-21T09:50:11</publicationDateTime>
        <placingProcedure><submissionCloseDateTime>{deadline}</submissionCloseDateTime></placingProcedure>
        <electronicPlaceInfo><name>РТС-тендер</name></electronicPlaceInfo>
        <lots>{lot_rows}</lots>
      </purchaseNoticeZKESMBOData></item></body>
    </purchaseNoticeZKESMBO>
    """


def _summary223():
    return parse_search_html(_row(NO_223, law="223-ФЗ", status="Работа комиссии", print_link=PRINT_223))[0]


@pytest.mark.parametrize(("raw", "timestamp", "day"), [
    (None, None, None),
    ("2026-11-30", None, date(2026, 11, 30)),
    ("2028-02-14+03:00", None, date(2028, 2, 14)),
    ("2026-02-30Z", None, None),
    ("2026-11-21T12:23:32Z", "2026-11-21T12:23:32+00:00", None),
    ("2026-11-21T12:23:32", "2026-11-21T12:23:32", None),
    ("без даты", None, None),
])
def test_source_date_preserves_precision_and_only_attested_zone(raw, timestamp, day):
    expected = datetime.fromisoformat(timestamp) if timestamp else None
    assert source_datetime(raw) == (expected, day)


def test_44_customer_and_dates_come_from_scoped_xml_fields():
    summary = parse_search_html(_row())[0]
    tender = parse_notice_xml(_xml44(), summary, include_documents=True)

    assert tender.customer.inn == "7701000012"
    assert tender.customer.full_name == "Учреждение заказчика"
    assert tender.customer.ogrn == "1230000000000"
    assert tender.customer.region_code == "77"
    assert tender.platform == Platform.RTS_TENDER
    assert tender.publish_date.isoformat() == "2026-11-21T12:23:32+03:00"
    assert tender.applications_deadline.isoformat() == "2026-11-30T09:00:00+03:00"
    assert tender.auction_date.isoformat() == "2026-11-30T11:00:00+03:00"
    assert tender.price_initial_rub == Decimal("17397.50")
    assert tender.okpd2_codes == ["22.29.25.000"]
    assert tender.okpd2_names == ["Принадлежности"]
    assert tender.ktru_codes == ["22.29.25.000-00000001"]
    assert [(doc.file_type, doc.size_bytes) for doc in tender.documents] == [
        (FileType.DOCX, 42), (FileType.XLSX, None),
    ]
    assert tender.status == TenderStatus.APPLICATIONS_OPEN


@pytest.mark.parametrize(("role", "code"), [("AUTH", "copied-org-code"), ("CU", "other-code")])
def test_distinct_organizer_does_not_supply_customer_inn(role, code):
    summary = parse_search_html(_row())[0]
    tender = parse_notice_xml(
        _xml44(
            role=role, customer_code=code, currency="USD", version="2",
            published="2026-11-23T12:23:32+03:00", deadline="",
        ),
        summary, include_documents=False,
    )
    assert not tender.customer.inn
    assert tender.customer.short_name == "Учреждение заказчика"
    assert tender.customer.region_code == "50"
    assert tender.publish_date is None and tender.publish_date_only == date(2026, 11, 21)
    assert tender.applications_deadline == summary.applications_deadline
    assert tender.currency == "USD" and tender.price_initial_rub is None
    assert tender.documents == []


@pytest.mark.parametrize("text", [_xml44(number="0358300000826000044"), _xml44(subject="  "), "<ep>"])
def test_wrong_or_incomplete_44_xml_is_never_returned_as_card(text):
    with pytest.raises(ParseError):
        parse_notice_xml(text, parse_search_html(_row())[0], include_documents=True)


@pytest.mark.parametrize(("customer_zone", "placer_zone", "source", "expected"), [
    ("4", "4", "2026-11-20T11:15:00", "2026-11-20T11:15:00+07:00"),
    ("4", "0", "2026-11-20T11:15:00", "2026-11-20T11:15:00"),
    ("", "0", "2026-11-20T11:15:00", "2026-11-20T11:15:00"),
    ("20", "20", "2026-11-20T11:15:00", "2026-11-20T11:15:00"),
    ("4", "4", "2026-11-20T11:15:00+05:00", "2026-11-20T11:15:00+05:00"),
])
def test_223_converts_only_matching_and_plausible_local_zone(customer_zone, placer_zone, source, expected):
    result = parse_notice_xml(
        _xml223(customer_offset=customer_zone, placer_offset=placer_zone, deadline=source),
        _summary223(), include_documents=False,
    )
    assert result.applications_deadline.isoformat() == expected
    assert result.publish_date.isoformat() == "2026-11-21T09:50:11"
    assert result.status == TenderStatus.IN_PROGRESS
    assert result.customer.region_code == "54"
    assert result.price_initial_rub == Decimal("300842.15")
    assert result.okpd2_codes == ["22.29.25.000"]
    assert result.platform == Platform.RTS_TENDER


@pytest.mark.parametrize(("lots", "price", "currency"), [
    ((), Decimal("17397.50"), "RUB"),
    ((("100", "USD"),), None, "USD"),
    ((("100", "USD"), ("30", "RUB")), None, "RUB"),
    ((("NaN", "RUB"),), None, "RUB"),
    ((("нет цены", "RUB"),), None, "RUB"),
    ((("10", ""),), None, "RUB"),
])
def test_223_prices_never_sum_incomplete_or_mixed_currency_lots(lots, price, currency):
    result = parse_notice_xml(_xml223(lots=lots), _summary223(), include_documents=False)
    assert result.price_initial_rub == price
    assert result.currency == currency


@pytest.mark.parametrize("xml", [_xml223(number="32616599715"), _xml223(subject="")])
def test_223_xml_must_match_notice_identity(xml):
    with pytest.raises(ParseError, match="223-ФЗ"):
        parse_notice_xml(xml, _summary223(), include_documents=True)


@pytest.mark.parametrize("url", [
    "", "https://[", "https://other.example" + PRINT_223, "/wrong/printForm/view.html",
    PRINT_223.replace(NO_223, "32616599715"), PRINT_223.replace(GUID, "bad"),
    PRINT_223 + f"&purchaseNoticeGuid={GUID}",
])
def test_223_rejects_incorrect_guid_print_link(url):
    assert print_links_223(url, NO_223) is None


@pytest.mark.parametrize(("label", "status"), [
    ("Подача заявок", TenderStatus.APPLICATIONS_OPEN),
    ("Ведётся приём заявок", TenderStatus.APPLICATIONS_OPEN),
    ("Подача заявок завершена", TenderStatus.APPLICATIONS_CLOSED),
    ("Подведение итогов", TenderStatus.IN_PROGRESS),
    ("Определение поставщика завершено", TenderStatus.COMPLETED),
    ("Закупка отменена после завершения", TenderStatus.CANCELLED),
    ("Извещение размещено", TenderStatus.PUBLISHED),
    ("Новый этап", TenderStatus.UNKNOWN),
])
def test_html_stage_uses_registry_label_and_preserves_day_without_midnight(label, status):
    first = _row(status=label)
    second = _row(status="Отменена").replace("Канцелярские товары", "Дубликат")
    result = parse_search_html(first + second)
    assert len(result) == 1
    row = result[0]
    assert row.status == status
    assert row.publish_date is None
    assert row.publish_date_only == date(2026, 11, 21)
    assert row.applications_deadline == datetime(2026, 11, 30, 9)
    assert row.customer.short_name == "Заказчик"
    assert row.customer.inn == "5403106778"
    assert row.customer.ogrn == "1025401307800"


@pytest.mark.parametrize("fragment", [
    '<div class="registry-entry__form">Нет номера</div>',
    _row("0000"),
    _row(law="Неподтверждённый закон"),
    _row(NO_223, law="44-ФЗ"),
    _row(card_path="https://other.example/card"),
    _row().replace("registry-entry__body-value", "missing-title"),
])
def test_incomplete_html_fails_instead_of_inserting_placeholder_notice(fragment):
    with pytest.raises(ParseError, match="неполную"):
        parse_search_html(fragment)


@pytest.mark.parametrize(("region", "address", "code"), [
    (None, "129110, г.Москва, Щепкина", "77"),
    ("Ростовская обл.", "Таганрог", "61"),
    (None, "Московская область, ул. Московская", "50"),
    ("Республика Татарстан (Татарстан)", None, "16"),
    ("Неизвестный регион", "ул. Московская, 1", None),
    (None, None, None),
])
def test_region_comes_from_explicit_name_or_address_component(region, address, code):
    parsed, _name = customer_region(region, address)
    assert parsed == code


def test_customer_location_codes_are_padded_deduplicated_and_checked_against_eis():
    assert normalize_customer_regions([" 5 ", "05", "50", "77"]) == ["05", "50", "77"]
    with pytest.raises(ValidationError) as exc:
        normalize_customer_regions(["99"])
    assert exc.value.details["region_codes"] == ["99"]


async def test_region_and_pagination_are_native_verified_and_use_requested_page(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = _regional_page(_row(), page=2)
    async with EisPublicProvider(_cfg(tmp_path), http=http) as provider:
        result = await provider.search_tenders(TenderSearchFilter(regions=["50"], limit=10, next_page_token="2"))
    assert http.get_text.await_args.args == (SEARCH,)
    assert http.get_text.await_args.kwargs["params"]["customerPlace"] == "50000000000"
    assert http.get_text.await_args.kwargs["params"]["customerPlaceCodes"] == "50000000000"
    assert http.get_text.await_args.kwargs["params"]["pageNumber"] == "2"
    assert result.total_estimated == 1500
    assert result.next_page_token == "3"
    assert result.tenders[0].customer.region_code == "50"
    http.aclose.assert_not_awaited()


async def test_multi_region_results_do_not_invent_individual_location(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = _regional_page(_row(), selection="77000000000,50000000000")
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    result = await provider.search_tenders(TenderSearchFilter(regions=["50", "77"]))
    assert result.tenders[0].customer.region_code is None
    assert result.next_page_token == "2"


@pytest.mark.parametrize(("response", "error"), [
    (_row(), "местоположение"),
    (_regional_page(_row(), selection="77000000000"), "местоположение"),
    (_regional_page(_row(), page=3), "другую страницу"),
    (_regional_page("<html>Неожиданный ответ</html>", page=2), "нераспознаваемую"),
])
async def test_search_refuses_widened_or_unrecognized_pages(tmp_path, response, error):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = response
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    # Изменение страницы проверяется до сопоставления фильтра.
    token = "2" if "pageNumber" in response and error != "местоположение" else None
    with pytest.raises(ParseError, match="распознаваемую" if error == "нераспознаваемую" else error):
        await provider.search_tenders(TenderSearchFilter(regions=["50"], next_page_token=token))


async def test_past_last_page_does_not_repeat_clamped_registry_page(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = _regional_page(_row(), page=2)
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    result = await provider.search_tenders(TenderSearchFilter(regions=["50"], next_page_token="3"))
    assert result.tenders == []
    assert result.next_page_token is None


async def test_search_fails_when_upstream_ignores_selected_law(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = _row()
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    with pytest.raises(ParseError, match="закона"):
        await provider.search_tenders(TenderSearchFilter(law_type=[LawType.FZ_223]))


async def test_exact_223_search_skips_similar_numbers_and_keeps_authoritative_times(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.side_effect = [
        _row("32616599715", law="223-ФЗ") + _row(NO_223, law="223-ФЗ", print_link=PRINT_223),
        _xml223(),
    ]
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    result = await provider.search_tenders(TenderSearchFilter(query=NO_223))
    assert [tender.reg_number for tender in result.tenders] == [NO_223]
    assert result.tenders[0].applications_deadline.isoformat() == "2026-11-20T11:15:00+07:00"
    assert result.next_page_token is None
    assert http.get_text.await_args.args == (XML_223,)
    assert http.get_text.await_args.kwargs["params"] == {
        "purchaseNoticeGuid": GUID, "purchaseNoticeNumber": NO_223,
    }


async def test_exact_search_refuses_mismatch_between_region_and_xml(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.side_effect = [
        _regional_page(_row(NO_223, law="223-ФЗ", print_link=PRINT_223)),
        _xml223(),
    ]
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    with pytest.raises(ParseError, match="расходится"):
        await provider.search_tenders(TenderSearchFilter(query=NO_223, regions=["50"]))


@pytest.mark.parametrize(("number", "page", "xml", "error"), [
    (NO_223, EMPTY, None, NotFoundError),
    (NO_223, _row(NO_223, law="223-ФЗ"), None, ParseError),
    (NO_44, EMPTY, NotFoundError("archive missing"), NotFoundError),
    (NO_44, _row(), NotFoundError("published XML missing"), ProviderUnavailableError),
])
async def test_detail_distinguishes_absent_notice_and_broken_live_xml(tmp_path, number, page, xml, error):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.side_effect = [page] if xml is None else [page, xml]
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    with pytest.raises(error):
        await provider.get_tender(number)
    assert http.get_text.await_count == (1 if xml is None else 2)


async def test_old_44_xml_can_survive_missing_registry_row(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.side_effect = [EMPTY, _xml44()]
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    record = await provider.get_tender(NO_44)
    assert http.get_text.await_args.args == (XML_44,)
    assert record.reg_number == NO_44
    assert record.status == TenderStatus.UNKNOWN
    assert record.applications_deadline.isoformat() == "2026-11-30T09:00:00+03:00"


async def test_exact_44_keeps_confirmed_region_if_xml_omits_customer_location(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.side_effect = [
        _regional_page(_row()),
        f"<ep><commonInfo><purchaseNumber>{NO_44}</purchaseNumber>"
        "<purchaseObjectInfo>Товары</purchaseObjectInfo></commonInfo></ep>",
    ]
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    result = await provider.search_tenders(TenderSearchFilter(query=NO_44, regions=["50"]))
    record = result.tenders[0]
    assert record.customer.short_name == "Заказчик"
    assert record.customer.region_code == "50"
    assert record.price_initial_rub == Decimal("17397.50")


@pytest.mark.parametrize(("number", "protocols"), [("invalid", False), (NO_44, True)])
async def test_invalid_notice_number_or_protocols_do_not_make_network_requests(tmp_path, number, protocols):
    http = AsyncMock(spec=HttpAdapter)
    with pytest.raises(ValidationError):
        await EisPublicProvider(_cfg(tmp_path), http=http).get_tender(number, include_protocols=protocols)
    http.get_text.assert_not_awaited()


async def test_org_identity_is_required_and_absent_org_does_not_check_registry(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = EMPTY
    provider = EisPublicProvider(_cfg(tmp_path), http=http)
    with pytest.raises(ParseError, match="ИНН"):
        await provider.get_customer_history()
    http.get_text.assert_not_awaited()
    with pytest.raises(NotFoundError):
        await provider.get_customer_history(ogrn="1027800000000")
    assert http.get_text.await_count == 1
    assert http.get_text.await_args.args == (ORG_URL,)


def test_org_card_ignores_unrelated_org_and_rnp_requires_recognized_record():
    inn = "7801000012"
    html = f"""
    <div class="registry-entry__header">
        <div>Не та организация</div>
    </div>
    <div class="registry-entry__header">
        <div class="registry-entry__header-mid__number">Нужное учреждение</div>
        <div class="registry-entry__body-block">
            <div class="registry-entry__body-title">Местонахождение</div>
            <div class="registry-entry__body-value">Санкт-Петербург</div>
        </div>
        <div class="registry-entry__body-block"><span>{inn}</span></div>
    </div>
    """
    assert parse_org_html(html, inn) == {
        "name": "Нужное учреждение", "inn": inn, "ogrn": None,
        "address": "Санкт-Петербург", "region_code": "78",
    }
    assert parse_org_html(f"<div>{inn}</div>", inn) is None
    assert parse_rnp_status(f"<div>{inn}</div>", inn) == "unknown"
    assert parse_rnp_status("<div>Другая запись</div>", inn) == "clean"


@pytest.mark.parametrize(("ancestor", "law"), [
    ("export44", LawType.FZ_44),
    ("export223", LawType.FZ_223),
    ("rkpoNotification", LawType.PP_615),
    ('oldNotice xmlns="urn:44fz"', LawType.FZ_44),
    ('oldNotice xmlns="urn:223"', LawType.FZ_223),
    ("oldNotice", LawType.FZ_44),
])
async def test_legacy_xml_preserves_law_amount_fields_and_attachment_types(tmp_path, ancestor, law):
    names = ("notice.PDF", "spec.docx", "draft.doc", "lots.xlsx", "estimate.xls", "terms.rtf", "files.zip", "raw.bin")
    attachments = "".join(
        f"<attachment><fileName>{name}</fileName><url>{HOST}/{name}</url></attachment>"
        for name in names
    )
    root_tag = ancestor.split()[0]
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = f"""
    <{ancestor}>
        <purchaseObjectInfo> Старое извещение </purchaseObjectInfo>
        <purchaseObjectInfoFull> Описание </purchaseObjectInfoFull><INN>7701000012</INN>
        <OGRN>1230000000000</OGRN><shortName>Учреждение</shortName>
        <regionCode>77</regionCode><factAddress>Москва</factAddress>
        <publishDate>2026-11-20T10:00:00Z</publishDate>
        <applicationDeadline>2026-11-30T09:00:00+03:00</applicationDeadline>
        <auctionDate>не определена</auctionDate><maxPrice>17 397,50</maxPrice>
        <smallBusinessRequirement>Y</smallBusinessRequirement>
        {attachments}<attachmentInfo><docDescription>Нет ссылки</docDescription></attachmentInfo>
    </{root_tag}>
    """
    config = replace(_cfg(tmp_path), allow_html_scraping=True)
    async with HtmlFallbackProvider(config, http=http) as provider:
        assert provider.is_configured
        result = await provider.get_tender(NO_44)
    assert result.law_type == law
    assert result.title == "Старое извещение" and result.title_full == "Описание"
    assert result.price_initial_rub == Decimal("17397.50")
    assert result.publish_date.isoformat() == "2026-11-20T10:00:00+00:00"
    assert result.applications_deadline.isoformat() == "2026-11-30T09:00:00+03:00"
    assert result.auction_date is None
    assert result.smp_only
    assert result.source_provider == "html_fallback"
    assert [doc.file_type for doc in result.documents] == [
        FileType.PDF, FileType.DOCX, FileType.DOC, FileType.XLSX, FileType.XLS,
        FileType.RTF, FileType.ZIP, FileType.OTHER,
    ]
    assert http.get_text.await_args.kwargs["expected_404_to_not_found"] is True
    http.aclose.assert_not_awaited()


@pytest.mark.parametrize(("text", "exception"), [
    ("", NotFoundError), ("<html><body>Нет карточки</body></html>", NotFoundError), ("<bad>", ParseError),
])
async def test_legacy_xml_rejects_empty_html_or_invalid_xml(tmp_path, text, exception):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = text
    provider = HtmlFallbackProvider(_cfg(tmp_path), http=http)
    assert not provider.is_configured
    with pytest.raises(exception):
        await provider.get_tender(NO_44)


async def test_legacy_blank_fields_stay_null_and_documents_can_be_disabled(tmp_path):
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = (
        "<export44><purchaseObjectInfo> </purchaseObjectInfo><maxPrice>нет цены</maxPrice>"
        "<attachment><fileName>test.pdf</fileName><url>/file</url></attachment></export44>"
    )
    provider = HtmlFallbackProvider(_cfg(tmp_path), http=http)
    result = await provider.get_tender(NO_44, include_documents=False)
    assert result.title == "—"
    assert result.customer.inn == ""
    assert result.price_initial_rub is None
    assert result.applications_deadline is None
    assert result.documents == []
    with pytest.raises(NotImplementedError):
        await provider.search_tenders(TenderSearchFilter())
