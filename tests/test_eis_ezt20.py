"""Регрессии поиска и карточек электронных закупок у единственного поставщика."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from mcp_zakupki.config import AppConfig
from mcp_zakupki.errors import NotFoundError, ParseError, ProviderUnavailableError
from mcp_zakupki.providers.eis_notice_xml import parse_notice_xml
from mcp_zakupki.providers.eis_public import EisPublicProvider, parse_search_html
from mcp_zakupki.providers.http_client import HttpAdapter
from mcp_zakupki.providers.resolver import ProviderResolver
from mcp_zakupki.schemas import TenderSearchFilter, TenderStatus

from .test_eis_public import SEARCH_HTML

NUMBER = "0319200040826000036"
SUBJECT = "Бумага для аппаратов и приборов, кроме факса"
REGISTRY_URL = f"https://zakupki.gov.ru/epz/order/notice/ezt20/view/common-info.html?regNumber={NUMBER}"
XML_URL = "https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html"

# Минимальная форма строки EZT20, которую ЕИС вернула для запроса «бумага».
EZT20_ROW = f"""
<div class="registry-entry__form">
  <div class="registry-entry__header">
    <div class="registry-entry__header-top__title">
      44-ФЗ Закупка, осуществляемая в соответствии с частью 12 статьи 93 Закона № 44-ФЗ
    </div>
    <div class="registry-entry__header-mid__number">
      <a href="{REGISTRY_URL}">№ {NUMBER}</a>
    </div>
    <div class="registry-entry__header-mid__title">Определение поставщика завершено</div>
  </div>
  <div class="registry-entry__body">
    <div class="registry-entry__body-block">
      <div class="registry-entry__body-title">Заказчик</div>
      <div class="registry-entry__body-href">Учреждение</div>
    </div>
  </div>
  <div class="price-block__value">8&nbsp;928,00 ₽</div>
  <div class="data-block__title">Размещено</div>
  <div class="data-block__value">23.11.2026</div>
</div>
"""

ORDINARY_ROW = SEARCH_HTML.replace(
    '<div class="registry-entry__header">',
    '<div class="registry-entry__form">',
)
TAIL_ROW = ORDINARY_ROW.replace("0131200001026005946", "0131200001026005947")
PAGE = (
    ORDINARY_ROW + EZT20_ROW + TAIL_ROW
    + '<div class="search-results__total">17 записей</div>'
    + '<a class="paginator-button-next" data-pagenumber="2"></a>'
)


def _ezt20_xml(*positions: str) -> str:
    body = "".join(
        f"<o:purchaseObject><o:name>{subject}</o:name>"
        f"<o:KTRU><o:code>17.12.14.110-0000000{index}</o:code></o:KTRU></o:purchaseObject>"
        for index, subject in enumerate(positions, 1)
    )
    return f"""
    <o:epNotificationEZT2020 xmlns:o="urn:eis" xmlns:m="urn:common">
      <versionNumber>1</versionNumber>
      <commonInfo>
        <purchaseNumber>{NUMBER}</purchaseNumber>
        <publishDTInEIS>2026-11-23T14:16:30+07:00</publishDTInEIS>
      </commonInfo>
      <notificationInfo>
        <contractConditionsInfo><maxPriceInfo><maxPrice>8928.00</maxPrice></maxPriceInfo></contractConditionsInfo>
        <purchaseObjectsInfo>{body}</purchaseObjectsInfo>
      </notificationInfo>
    </o:epNotificationEZT2020>
    """


async def test_keyword_page_keeps_known_ezt20_row_in_original_order(tmp_path):
    config = AppConfig.from_env({"MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "unused.sqlite")})
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = PAGE
    provider = EisPublicProvider(config, http=http)
    result = await provider.search_tenders(TenderSearchFilter(query="бумага", limit=10))

    assert [tender.reg_number for tender in result.tenders] == [
        "0131200001026005946", NUMBER, "0131200001026005947",
    ]
    row = result.tenders[1]
    assert row.title == ""
    assert row.customer.short_name == "Учреждение"
    assert row.price_initial_rub == Decimal("8928.00")
    assert row.status == TenderStatus.COMPLETED
    assert row.publish_date_only == date(2026, 11, 23)
    assert row.url_eis == REGISTRY_URL
    assert result.next_page_token == "2" and result.total_estimated == 17
    assert http.get_text.await_count == 1


def test_ezt20_title_comes_from_subject_only():
    html = EZT20_ROW.replace(
        '<div class="registry-entry__body-href">Учреждение</div>',
        '<div class="registry-entry__body-value">Учреждение</div>',
    )
    assert parse_search_html(html)[0].title == ""
    named = html.replace(
        '<div class="registry-entry__body">',
        '<div class="registry-entry__body"><div class="registry-entry__body-block">'
        '<div class="registry-entry__body-title">Объект закупки</div>'
        '<div class="registry-entry__body-value">Предмет</div></div>',
    )
    assert parse_search_html(named)[0].title == "Предмет"


@pytest.mark.parametrize("get_detail", [False, True])
async def test_ezt20_exact_search_and_detail_use_scoped_xml_subject(tmp_path, get_detail):
    config = AppConfig.from_env({"MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "unused.sqlite")})
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.side_effect = [EZT20_ROW, _ezt20_xml(SUBJECT)]
    provider = EisPublicProvider(config, http=http)
    if get_detail:
        tender = await provider.get_tender(NUMBER)
    else:
        result = await provider.search_tenders(TenderSearchFilter(query=NUMBER, limit=10))
        assert result.page_size == result.total_estimated == 1
        tender = result.tenders[0]
    assert tender.reg_number == NUMBER
    assert tender.title == SUBJECT
    assert tender.status == TenderStatus.COMPLETED
    assert tender.publish_date.isoformat() == "2026-11-23T14:16:30+07:00"
    assert tender.ktru_codes == ["17.12.14.110-00000001"]
    assert http.get_text.await_args.args == (XML_URL,)
    assert http.get_text.await_args.kwargs["params"] == {"regNumber": NUMBER}


def test_ezt20_multiple_subjects_keep_all_published_position_names():
    summary = parse_search_html(EZT20_ROW)[0]
    positions = (SUBJECT, "Бумага чертёжная")
    tender = parse_notice_xml(_ezt20_xml(*positions), summary, include_documents=False)
    assert tender.title == ""
    assert tender.title_full.splitlines() == list(positions)
    assert tender.ktru_codes == ["17.12.14.110-00000001", "17.12.14.110-00000002"]


def test_ezt20_subject_cannot_be_taken_from_unrelated_xml_fields():
    summary = parse_search_html(EZT20_ROW)[0]
    xml = _ezt20_xml().replace("<purchaseObjectsInfo>", "<purchaseObjectsInfo><name>Группа</name>")
    with pytest.raises(ParseError):
        parse_notice_xml(xml, summary, include_documents=False)


async def test_ezt20_search_preserves_error_when_matching_xml_is_unavailable(tmp_path):
    config = AppConfig.from_env({"MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "unused.sqlite")})
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.side_effect = [EZT20_ROW, NotFoundError("XML временно недоступен")]
    provider = EisPublicProvider(config, http=http)
    with pytest.raises(ProviderUnavailableError, match="не вернула его XML"):
        await provider.search_tenders(TenderSearchFilter(query=NUMBER, limit=10))


@pytest.mark.parametrize(("page", "diagnostic"), [
    (
        PAGE.replace("/notice/ezt20/", "/notice/ea20/"),
        {"reason": "invalid_notice_row", "row": 2, "rows": 3},
    ),
    (
        "<html><title>sig=private-token</title><body>Неожиданная разметка</body></html>",
        {"reason": "unknown_search_page", "requested_page": 1},
    ),
])
async def test_broken_search_stays_error_with_safe_context(tmp_path, page, diagnostic):
    config = AppConfig.from_env({"MCP_ZAKUPKI_CACHE_DB": str(tmp_path / "unused.sqlite")})
    http = AsyncMock(spec=HttpAdapter)
    http.get_text.return_value = page
    provider = EisPublicProvider(config, http=http)
    async with ProviderResolver(config, chain=[provider]) as resolver:
        with pytest.raises(ProviderUnavailableError) as error:
            await resolver.search_tenders(TenderSearchFilter(query="скрытая строка"))
    result = error.value.to_dict()
    assert result["error"] == "provider_unavailable"
    assert result["details"] == {
        "operation": "search_tenders",
        "attempted": [{"provider": "eis_public", "reason": "ParseError"}],
        "last_error": "ParseError",
        "eis_search": diagnostic,
    }
    assert "sig=private-token" not in repr(result)
    assert "скрытая строка" not in repr(result)
