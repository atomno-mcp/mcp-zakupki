"""Официальный публичный источник ЕИС: zakupki.gov.ru.

Без токена машинного обмена сайт отдаёт HTML-поиск, карточку организации,
реестр недобросовестных поставщиков и XML-печатную форму извещения.
Сводную аналитику объёмов (как у покупного посредника) сайт не считает —
это честный отказ, не пустая сводка.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..config import AppConfig
from ..errors import NotFoundError, ParseError, ProviderUnavailableError
from ..schemas import (
    Customer,
    LawType,
    OrgHistorySummary,
    Platform,
    SearchMeta,
    SearchResult,
    Tender,
    TenderStatus,
)
from .base import BaseProvider, ProviderCapability, ProviderInfo
from .html_fallback import (
    _BASE as _EIS_BASE,
)
from .html_fallback import (
    _VIEW_HTML,
    _VIEW_XML,
    _xml_to_tender,
)
from .http_client import HttpAdapter

if TYPE_CHECKING:
    from ..schemas import TenderSearchFilter

logger = logging.getLogger(__name__)

_SEARCH_PATH = "/epz/order/extendedsearch/results.html"
_ORG_SEARCH_PATH = "/epz/organization/search/results.html"
_RNP_SEARCH_PATH = "/epz/dishonestsupplier/search/results.html"

_NO_RECORDS_RE = re.compile(
    r"Поиск не дал результатов|noRecords",
    re.IGNORECASE,
)
_REG_RE = re.compile(r"regNumber=(\d{18,25})")
_PRICE_RE = re.compile(
    r"([\d\s\u00a0]+(?:[.,]\d{1,2})?)\s*(?:₽|руб)",
    re.IGNORECASE,
)
_INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")

_ANALYTICS_UNAVAILABLE = (
    "Сводная статистика поставщика (объёмы, средние, помесячно) на "
    "официальном сайте zakupki.gov.ru без токена машинного обмена недоступна. "
    "Покупной посредник отключён. Машинный обмен ЕИС (SOAP getDocsIP) "
    "требует регистрацию на портале и токен — без аккредитации его не выдают."
)


class EisPublicProvider(BaseProvider):
    """Публичные страницы и XML-печатные формы zakupki.gov.ru."""

    def __init__(self, config: AppConfig, *, http: HttpAdapter | None = None) -> None:
        self._config = config
        self._http = http or HttpAdapter(config)
        self._owns_http = http is None

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="eis_public",
            label="ЕИС (zakupki.gov.ru)",
            capabilities=ProviderCapability.SEARCH
            | ProviderCapability.TENDER_DETAILS
            | ProviderCapability.CUSTOMER_HISTORY
            | ProviderCapability.SUPPLIER_STATS,
            requires_auth=False,
        )

    @property
    def is_configured(self) -> bool:
        return True

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search_tenders(self, filter_: TenderSearchFilter) -> SearchResult:
        params = _search_params(filter_)
        text = await self._http.get_text(
            f"{_EIS_BASE}{_SEARCH_PATH}",
            provider_name=self.info.label,
            params=params,
        )
        tenders = parse_search_html(text)
        return SearchResult(
            query_echo=filter_.model_dump(mode="json"),
            total_estimated=None,
            page_size=len(tenders),
            next_page_token=_next_page(filter_, tenders),
            tenders=tenders,
            search_meta=SearchMeta(took_ms=0, source="eis_public", cache_hit=False),
        )

    async def get_tender(
        self,
        reg_number: str,
        *,
        include_documents: bool = True,
        include_protocols: bool = False,
    ) -> Tender:
        url = f"{_EIS_BASE}{_VIEW_XML}"
        text = await self._http.get_text(
            url,
            provider_name=self.info.label,
            params={"regNumber": reg_number},
            expected_404_to_not_found=True,
        )
        if not text or "<html" in text[:200].lower():
            raise NotFoundError(
                f"Тендер {reg_number} не найден по публичному XML-виду ЕИС.",
                details={"reg_number": reg_number, "url": f"{url}?regNumber={reg_number}"},
            )
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ParseError(
                f"ЕИС: невалидный XML печатной формы {reg_number}.",
                details={"reg_number": reg_number},
            ) from exc
        tender = _xml_to_tender(
            root,
            reg_number,
            url_xml=f"{url}?regNumber={reg_number}",
            url_eis=f"{_EIS_BASE}{_VIEW_HTML}?regNumber={reg_number}",
            include_documents=include_documents,
        )
        return tender.model_copy(update={"source_provider": "eis_public"})

    async def get_customer_history(
        self,
        *,
        inn: str | None = None,
        ogrn: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> OrgHistorySummary:
        ident = inn or ogrn or ""
        if not ident:
            raise ParseError(
                "ЕИС: для карточки заказчика нужен ИНН или ОГРН.",
                details={"provider": "eis_public"},
            )
        org_html = await self._http.get_text(
            f"{_EIS_BASE}{_ORG_SEARCH_PATH}",
            provider_name=self.info.label,
            params={
                "searchString": ident,
                "morphology": "on",
                "pageNumber": "1",
                "recordsPerPage": "_10",
            },
        )
        card = parse_org_html(org_html, ident)
        if card is None:
            raise NotFoundError(
                f"Организация {ident} не найдена в реестре организаций ЕИС.",
                details={"inn": inn, "ogrn": ogrn, "provider": "eis_public"},
            )
        rnp_html = await self._http.get_text(
            f"{_EIS_BASE}{_RNP_SEARCH_PATH}",
            provider_name=self.info.label,
            params={"searchString": ident, "pageNumber": "1"},
        )
        rnp_status = parse_rnp_status(rnp_html, ident)
        return OrgHistorySummary(
            org_inn=card["inn"] or ident,
            org_role="customer",
            short_name=card["name"],
            region_code=card.get("region_code"),
            period_from=period_from or "",
            period_to=period_to or "",
            tenders_total=None,
            tenders_completed=None,
            tenders_cancelled=None,
            total_volume_rub=None,
            avg_contract_rub=None,
            median_contract_rub=None,
            rnp_status=rnp_status,
            coverage="identity",
            limitations=(
                "Официальный сайт отдал карточку организации и статус в реестре "
                "недобросовестных поставщиков. Сводку объёмов и числа закупок "
                "без токена машинного обмена ЕИС сайт не считает."
            ),
            fetched_at=datetime.now(UTC),
            source_provider="eis_public",
        )

    async def get_supplier_stats(
        self,
        *,
        inn: str | None = None,
        ogrn: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> OrgHistorySummary:
        raise ProviderUnavailableError(
            _ANALYTICS_UNAVAILABLE,
            details={
                "provider": "eis_public",
                "capability": "supplier_stats",
                "inn": inn,
                "ogrn": ogrn,
            },
        )


def _search_params(filter_: TenderSearchFilter) -> dict[str, Any]:
    page = 1
    if filter_.next_page_token and str(filter_.next_page_token).isdigit():
        page = max(1, int(filter_.next_page_token))
    per_page = "_10" if filter_.limit <= 10 else "_20" if filter_.limit <= 20 else "_50"
    query = filter_.query or filter_.customer_inn or ""
    params: dict[str, Any] = {
        "searchString": query,
        "morphology": "on",
        "pageNumber": str(page),
        "sortDirection": "false",
        "recordsPerPage": per_page,
        "showLotsInfoHidden": "false",
        "sortBy": "UPDATE_DATE",
        "currencyIdGeneral": "-1",
    }
    laws = {_as_str(lt) for lt in filter_.law_type}
    if not laws or "44-fz" in laws:
        params["fz44"] = "on"
    if "223-fz" in laws:
        params["fz223"] = "on"
    if "615-pp" in laws:
        params["ppRf615"] = "on"
    if filter_.customer_inn and filter_.query:
        params["searchString"] = filter_.query
        params["customerInn"] = filter_.customer_inn
    return params


def _next_page(filter_: TenderSearchFilter, tenders: list[Tender]) -> str | None:
    if not tenders:
        return None
    page = 1
    if filter_.next_page_token and str(filter_.next_page_token).isdigit():
        page = int(filter_.next_page_token)
    if len(tenders) < min(filter_.limit, 10):
        return None
    return str(page + 1)


def _as_str(v: Any) -> str:
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)


def parse_search_html(html: str) -> list[Tender]:
    """Разобрать страницу расширенного поиска ЕИС в список карточек."""
    blocks = re.split(r'class="registry-entry__header"', html)
    seen: set[str] = set()
    tenders: list[Tender] = []
    for block in blocks[1:]:
        tender = _parse_search_block(block)
        if tender is None or tender.reg_number in seen:
            continue
        seen.add(tender.reg_number)
        tenders.append(tender)
    return tenders


def _parse_search_block(block: str) -> Tender | None:
    regs = _REG_RE.findall(block)
    if not regs:
        return None
    reg_number = regs[0]
    tree = HTMLParser(f"<div {block}")
    title = _first_text(tree, ".registry-entry__body-value") or "—"
    customer_name = _first_text(tree, ".registry-entry__body-href a") or "—"
    header = _first_text(tree, ".registry-entry__header-top__title") or ""
    status_raw = _first_text(tree, ".registry-entry__header-mid__title") or ""
    price_raw = _first_text(tree, ".price-block") or ""
    return Tender(
        reg_number=reg_number,
        law_type=_law_from_header(header),
        title=_collapse(title),
        customer=Customer(inn="", short_name=_collapse(customer_name)),
        price_initial_rub=_parse_price(price_raw),
        platform=Platform.OTHER,
        status=_status_from_ru(status_raw),
        url_eis=urljoin(_EIS_BASE, f"{_VIEW_HTML}?regNumber={reg_number}"),
        url_xml=urljoin(_EIS_BASE, f"{_VIEW_XML}?regNumber={reg_number}"),
        fetched_at=datetime.now(UTC),
        source_provider="eis_public",
    )


def parse_org_html(html: str, ident: str) -> dict[str, str | None] | None:
    """Карточка организации из реестра ЕИС. None — организация не найдена."""
    if _NO_RECORDS_RE.search(html) and ident not in html:
        return None
    blocks = re.split(r'class="registry-entry__header"', html)
    ident_digits = re.sub(r"\D", "", ident)
    for block in blocks[1:]:
        if ident_digits and ident_digits not in block:
            continue
        tree = HTMLParser(f"<div {block}")
        name = _first_text(tree, ".registry-entry__header-mid__number a") or _first_text(
            tree, ".registry-entry__header-mid__number"
        )
        fields = _labeled_values(tree)
        inn = fields.get("ИНН") or (ident_digits if len(ident_digits) in {10, 12} else "")
        if not name and not inn:
            continue
        address = fields.get("Местонахождение")
        return {
            "name": _collapse(name or "—"),
            "inn": inn,
            "ogrn": fields.get("ОГРН"),
            "address": address,
            "region_code": _region_from_address(address),
        }
    if ident_digits and ident_digits in html:
        # Страница открылась и содержит ИНН, но разметку не разобрали.
        return None
    return None


def parse_rnp_status(html: str, ident: str) -> str:
    """clean / in_rnp / unknown по HTML реестра недобросовестных."""
    ident_digits = re.sub(r"\D", "", ident)
    if _NO_RECORDS_RE.search(html):
        return "clean"
    if ident_digits and ident_digits in html and 'class="registry-entry' in html:
        return "in_rnp"
    if ident_digits and ident_digits not in html:
        return "clean"
    return "unknown"


def _labeled_values(tree: HTMLParser) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.css(".registry-entry__body-block"):
        title = _collapse(node.css_first(".registry-entry__body-title").text() if node.css_first(".registry-entry__body-title") else "")
        value = _collapse(node.css_first(".registry-entry__body-value").text() if node.css_first(".registry-entry__body-value") else "")
        if title and value:
            out[title] = value
    return out


def _first_text(tree: HTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    text = _collapse(node.text())
    return text or None


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _law_from_header(header: str) -> LawType:
    if "223" in header:
        return LawType.FZ_223
    if "615" in header:
        return LawType.PP_615
    return LawType.FZ_44


def _status_from_ru(raw: str) -> TenderStatus:
    s = raw.lower()
    if "подача заявок" in s:
        return TenderStatus.APPLICATIONS_OPEN
    if "работа комисс" in s or "рассмотрение" in s:
        return TenderStatus.APPLICATIONS_CLOSED
    if "завершен" in s or "исполнен" in s:
        return TenderStatus.COMPLETED
    if "отмен" in s:
        return TenderStatus.CANCELLED
    if "размещен" in s:
        return TenderStatus.PUBLISHED
    return TenderStatus.UNKNOWN


def _parse_price(raw: str) -> Decimal | None:
    m = _PRICE_RE.search(raw.replace("\u00a0", " "))
    if not m:
        return None
    num = m.group(1).replace(" ", "").replace(",", ".")
    try:
        return Decimal(num)
    except (InvalidOperation, ValueError):
        return None


def _region_from_address(address: str | None) -> str | None:
    if not address:
        return None
    if "МОСКВА" in address.upper():
        return "77"
    if "САНКТ-ПЕТЕРБУРГ" in address.upper() or "САНКТ ПЕТЕРБУРГ" in address.upper():
        return "78"
    return None


__all__ = [
    "EisPublicProvider",
    "parse_org_html",
    "parse_rnp_status",
    "parse_search_html",
]
