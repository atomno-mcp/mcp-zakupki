"""Разбор официальных страниц ЕИС и отказ сводки поставщика."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from mcp_zakupki.config import AppConfig
from mcp_zakupki.errors import ProviderUnavailableError
from mcp_zakupki.providers.eis_public import (
    EisPublicProvider,
    parse_org_html,
    parse_rnp_status,
    parse_search_html,
)
from mcp_zakupki.schemas import TenderSearchFilter

SEARCH_HTML = """
<div class="registry-entry__header">
  <div class="registry-entry__header-top__title">44-ФЗ Запрос котировок</div>
  <a href="/epz/order/notice/printForm/view.html?regNumber=0131200001026005946">печать</a>
  <div class="registry-entry__header-mid__number">
    <a href="/epz/order/notice/zk20/view/common-info.html?regNumber=0131200001026005946">№ 0131200001026005946</a>
  </div>
  <div class="registry-entry__header-mid__title">Подача заявок</div>
  <div class="registry-entry__body-value">Поставка расходных материалов</div>
  <div class="registry-entry__body-href">
    <a href="/epz/organization/view/info.html">МИНЗАКАЗ ВО</a>
  </div>
  <div class="price-block">Начальная цена 58 540,00 ₽</div>
</div>
"""

ORG_HTML = """
<div class="registry-entry__header">
  <div class="registry-entry__header-mid__number">
    <a onclick="getInformation(0, '/x', '/epz/organization/view/info.html?organizationId=2060921')">
      ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО "СБЕРБАНК РОССИИ"
    </a>
  </div>
  <div class="registry-entry__body-block">
    <div class="registry-entry__body-title">Местонахождение</div>
    <div class="registry-entry__body-value">117312, Г.МОСКВА, УЛ. ВАВИЛОВА, Д.19</div>
  </div>
  <div class="registry-entry__body-block">
    <div class="registry-entry__body-title">ОГРН</div>
    <div class="registry-entry__body-value">1027700132195</div>
  </div>
  <div class="registry-entry__body-block">
    <div class="registry-entry__body-title">ИНН</div>
    <div class="registry-entry__body-value">7707083893</div>
  </div>
</div>
"""

RNP_EMPTY = '<p class="noRecords">Поиск не дал результатов</p>'
RNP_HIT = """
<div class="registry-entry__header">
  <div>7707083893</div>
</div>
"""

TENDER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ns7:epNotificationEZK2020 xmlns:ns7="http://zakupki.gov.ru/oos/printform/1">
  <commonInfo>
    <purchaseNumber>0131200001026005946</purchaseNumber>
    <purchaseObjectInfo>Поставка расходных материалов</purchaseObjectInfo>
  </commonInfo>
  <INN>3666159455</INN>
  <shortName>МИНЗАКАЗ ВО</shortName>
  <maxPrice>58540.00</maxPrice>
</ns7:epNotificationEZK2020>
"""


def _cfg() -> AppConfig:
    return AppConfig.from_env({"MCP_ZAKUPKI_CACHE_DB": "/tmp/eis_public_test.sqlite"})


class TestParsers:
    def test_search_html_extracts_reg_and_title(self) -> None:
        tenders = parse_search_html(SEARCH_HTML)
        assert len(tenders) == 1
        assert tenders[0].reg_number == "0131200001026005946"
        assert "расходных" in tenders[0].title
        assert tenders[0].source_provider == "eis_public"
        assert tenders[0].customer.short_name == "МИНЗАКАЗ ВО"

    def test_org_html_sber(self) -> None:
        card = parse_org_html(ORG_HTML, "7707083893")
        assert card is not None
        assert card["inn"] == "7707083893"
        assert "СБЕРБАНК" in (card["name"] or "")
        assert card["ogrn"] == "1027700132195"
        assert card["region_code"] == "77"

    def test_org_html_empty(self) -> None:
        assert parse_org_html('<p class="noRecords">Поиск не дал результатов</p>', "0000000000") is None

    def test_rnp_clean(self) -> None:
        assert parse_rnp_status(RNP_EMPTY, "7707083893") == "clean"

    def test_rnp_hit(self) -> None:
        assert parse_rnp_status(RNP_HIT, "7707083893") == "in_rnp"


@pytest.mark.asyncio
class TestEisPublicProvider:
    async def test_search(self) -> None:
        with respx.mock(assert_all_called=False) as router:
            router.get("https://zakupki.gov.ru/epz/order/extendedsearch/results.html").mock(
                return_value=Response(200, text=SEARCH_HTML, headers={"content-type": "text/html"})
            )
            async with EisPublicProvider(_cfg()) as p:
                res = await p.search_tenders(TenderSearchFilter(query="бумага"))
        assert res.tenders[0].reg_number == "0131200001026005946"
        assert res.search_meta.source == "eis_public"

    async def test_get_tender_xml(self) -> None:
        with respx.mock(assert_all_called=False) as router:
            router.get("https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html").mock(
                return_value=Response(200, text=TENDER_XML, headers={"content-type": "text/xml"})
            )
            async with EisPublicProvider(_cfg()) as p:
                tender = await p.get_tender("0131200001026005946")
        assert tender.reg_number == "0131200001026005946"
        assert tender.source_provider == "eis_public"
        assert "расходных" in tender.title

    async def test_customer_identity_not_volumes(self) -> None:
        with respx.mock(assert_all_called=False) as router:
            router.get("https://zakupki.gov.ru/epz/organization/search/results.html").mock(
                return_value=Response(200, text=ORG_HTML)
            )
            router.get("https://zakupki.gov.ru/epz/dishonestsupplier/search/results.html").mock(
                return_value=Response(200, text=RNP_EMPTY)
            )
            async with EisPublicProvider(_cfg()) as p:
                summary = await p.get_customer_history(inn="7707083893")
        assert summary.short_name and "СБЕРБАНК" in summary.short_name
        assert summary.rnp_status == "clean"
        assert summary.coverage == "identity"
        assert summary.tenders_total is None
        assert summary.total_volume_rub is None
        assert summary.limitations

    async def test_supplier_stats_refuses(self) -> None:
        async with EisPublicProvider(_cfg()) as p:
            with pytest.raises(ProviderUnavailableError) as exc:
                await p.get_supplier_stats(inn="7707083893")
        assert "машинного обмена" in exc.value.message_ru
