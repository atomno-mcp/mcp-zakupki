"""Официальный публичный источник ЕИС: zakupki.gov.ru.

Без токена машинного обмена сайт отдаёт HTML-поиск, карточку организации,
реестр недобросовестных поставщиков и XML-печатную форму извещения.
Сводную аналитику объёмов (как у покупного посредника) сайт не считает —
это честный отказ, не пустая сводка.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, urlsplit

from selectolax.parser import HTMLParser, Node

from ..config import AppConfig
from ..errors import NotFoundError, ParseError, ProviderUnavailableError, ValidationError
from ..schemas import (
    Customer,
    LawType,
    OrgHistorySummary,
    Platform,
    SearchMeta,
    SearchResult,
    Tender,
    TenderSearchFilter,
    TenderStatus,
)
from ..search_contract import MAX_PAGE, validate_public_search
from .base import BaseProvider, ProviderCapability, ProviderInfo
from .eis_notice_xml import is_notice_number, parse_notice_xml, print_links_223, public_eis_url
from .eis_regions import REGION_NAMES, normalize_customer_regions
from .html_fallback import (
    _BASE as _EIS_BASE,
)
from .html_fallback import _VIEW_HTML, _VIEW_XML
from .http_client import HttpAdapter

logger = logging.getLogger(__name__)

_SEARCH_PATH = "/epz/order/extendedsearch/results.html"
_ORG_SEARCH_PATH = "/epz/organization/search/results.html"
_RNP_SEARCH_PATH = "/epz/dishonestsupplier/search/results.html"

_NO_RECORDS_RE = re.compile(
    r"Поиск не дал результатов|noRecords",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    r"([\d\s\u00a0]+(?:[.,]\d{1,2})?)\s*(?:₽|руб)",
    re.IGNORECASE,
)
_INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
_HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

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
        tree = await self._search_page(params)
        page = int(params["pageNumber"])
        tenders = _page_tenders(tree, page)
        if any(tender.law_type not in filter_.law_type for tender in tenders) and filter_.law_type:
            raise ParseError("ЕИС вернула извещения вне выбранного закона.")
        tenders = _filter_customer_location(tree, params, tenders)
        query = filter_.query
        if query and is_notice_number(query):
            tenders = [
                await self._load_notice(tender, include_documents=False)
                for tender in tenders
                if tender.reg_number == query
            ]
            regions = normalize_customer_regions(filter_.regions)
            if regions and any(
                tender.customer.region_code and tender.customer.region_code not in regions
                for tender in tenders
            ):
                raise ParseError("Регион заказчика в карточке расходится с фильтром реестра ЕИС.")
            return _search_result(filter_, tenders, total=len(tenders))

        return _search_result(filter_, tenders, total=_search_total(tree), next_token=_next_page(tree, page))

    async def _search_page(self, params: dict[str, Any]) -> HTMLParser:
        text = await self._http.get_text(
            f"{_EIS_BASE}{_SEARCH_PATH}",
            provider_name=self.info.label,
            params=params,
            headers=_HTML_HEADERS,
        )
        return HTMLParser(text)

    async def get_tender(
        self,
        reg_number: str,
        *,
        include_documents: bool = True,
        include_protocols: bool = False,
    ) -> Tender:
        if include_protocols:
            raise ValidationError("Публичный источник ЕИС не поддерживает загрузку протоколов.")
        if not is_notice_number(reg_number):
            raise ValidationError("Номер извещения должен содержать 11 или 19–21 цифру.")

        is_223 = len(reg_number) == 11
        tree = await self._search_page(_search_params(TenderSearchFilter(
            query=reg_number, limit=10,
            law_type=[LawType.FZ_223] if is_223 else [LawType.FZ_44, LawType.PP_615],
        )))
        summary = next((item for item in _page_tenders(tree, 1) if item.reg_number == reg_number), None)
        if summary is None and is_223:
            raise NotFoundError(
                f"Извещение {reg_number} не найдено в публичном реестре ЕИС.",
                details={"reg_number": reg_number},
            )
        if summary is None:
            # У старого извещения может остаться печатная форма без строки
            # в реестре. Для таких карточек этап остаётся неизвестным.
            archived = Tender(
                reg_number=reg_number, law_type=LawType.FZ_44, title="",
                customer=Customer(inn="", short_name="—"),
                url_eis=f"{_EIS_BASE}{_VIEW_HTML}?regNumber={reg_number}",
                url_xml=f"{_EIS_BASE}{_VIEW_XML}?regNumber={reg_number}",
                fetched_at=datetime.now(UTC), source_provider=self.name,
            )
            return await self._load_notice(archived, include_documents=include_documents, listed=False)
        return await self._load_notice(summary, include_documents=include_documents)

    async def _load_notice(self, summary: Tender, *, include_documents: bool, listed: bool = True) -> Tender:
        if not summary.url_xml:
            raise ParseError("В карточке 223-ФЗ отсутствует ссылка ЕИС на XML извещения.")
        parts = urlsplit(summary.url_xml)
        try:
            text = await self._http.get_text(
                f"{_EIS_BASE}{parts.path}",
                provider_name=self.info.label,
                params={name: values[0] for name, values in parse_qs(parts.query).items()},
                headers=_HTML_HEADERS,
                expected_404_to_not_found=True,
            )
        except NotFoundError as exc:
            if not listed:
                raise
            raise ProviderUnavailableError(
                "ЕИС показала извещение в реестре, но не вернула его XML.",
                details={"reg_number": summary.reg_number},
            ) from exc
        return parse_notice_xml(text, summary, include_documents=include_documents)

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
    page = validate_public_search(filter_)
    per_page = f"_{filter_.limit}"
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
    codes = normalize_customer_regions(filter_.regions)
    if codes:
        kladr = ",".join(f"{code}000000000" for code in codes)
        params.update(customerPlace=kladr, customerPlaceCodes=kladr)
    return params


def _search_result(
    filter_: TenderSearchFilter,
    tenders: list[Tender],
    *,
    total: int | None = None,
    next_token: str | None = None,
) -> SearchResult:
    return SearchResult(
        query_echo=filter_.model_dump(mode="json"),
        total_estimated=total,
        page_size=len(tenders),
        next_page_token=next_token,
        tenders=tenders,
        search_meta=SearchMeta(took_ms=0, source="eis_public", cache_hit=False),
    )


def _active_page(tree: HTMLParser) -> int | None:
    current = _first_text(tree, ".paginator .page__link_active")
    if current and current.isdecimal():
        return int(current)
    node = tree.css_first("#pageNumber")
    raw = node.attributes.get("value", "") if node else ""
    return int(raw) if raw.isdecimal() else None


def _search_total(tree: HTMLParser) -> int | None:
    raw = _first_text(tree, ".search-results__total")
    if not raw:
        return None
    match = re.fullmatch(r"(?:более\s+)?([0-9 ]+)\s+запис\w*", raw, re.IGNORECASE)
    return int(match.group(1).replace(" ", "")) if match else None


def _next_page(tree: HTMLParser, page: int) -> str | None:
    node = tree.css_first(".paginator-button-next[data-pagenumber]")
    token = node.attributes.get("data-pagenumber", "") if node else ""
    if _active_page(tree) not in {None, page}:
        return None
    return token if page < MAX_PAGE and token == str(page + 1) else None


def _page_tenders(tree: HTMLParser, page: int) -> list[Tender]:
    active = _active_page(tree)
    if active is not None and active < page:
        # При запросе за концом выдачи ЕИС может заново отрисовать последнюю страницу.
        return []
    if active is not None and active > page:
        raise ParseError("ЕИС вернула другую страницу поиска.")
    tenders = _parse_search_cards(tree)
    if tenders or tree.css_first(".noRecords") or _search_total(tree) == 0:
        return tenders
    raise ParseError("ЕИС не вернула распознаваемую страницу результатов поиска.")


def _filter_customer_location(
    tree: HTMLParser,
    params: dict[str, Any],
    tenders: list[Tender],
) -> list[Tender]:
    requested = params.get("customerPlace")
    if not requested:
        return tenders
    kladr = set(requested.split(","))
    for field in ("customerPlace", "customerPlaceCodes"):
        node = tree.css_first(f'input[name="{field}"]')
        returned = node.attributes.get("value", "") if node else ""
        if set(returned.split(",")) != kladr:
            raise ParseError("ЕИС не подтвердила выбранное местоположение заказчика.")
    if len(kladr) == 1:
        code = requested[:2]
        return [
            tender.model_copy(update={
                "customer": tender.customer.model_copy(update={
                    "region_code": code, "region_name": REGION_NAMES[code],
                }),
            })
            for tender in tenders
        ]
    return tenders


def _as_str(v: Any) -> str:
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)


def parse_search_html(html: str) -> list[Tender]:
    """Разобрать карточки расширенного поиска ЕИС."""
    return _parse_search_cards(HTMLParser(html))


def _parse_search_cards(tree: HTMLParser) -> list[Tender]:
    blocks = tree.css(".registry-entry__form") or tree.css(".registry-entry__header")
    tenders: dict[str, Tender] = {}
    for block in blocks:
        tender = _parse_search_block(block)
        if tender is None:
            raise ParseError("ЕИС вернула неполную разметку извещения в результатах.")
        tenders.setdefault(tender.reg_number, tender)
    return list(tenders.values())


def _customer_from_card(block: Node) -> Customer:
    for row in block.css(".registry-entry__body-block"):
        if _first_text(row, ".registry-entry__body-title") not in {"Заказчик", "Заказчики"}:
            continue
        link = row.css_first(".registry-entry__body-href a")
        name = _first_text(row, ".registry-entry__body-href") or _first_text(row, ".registry-entry__body-value")
        href = link.attributes.get("href", "") if link else ""
        params = parse_qs(urlsplit(href).query)
        return Customer(
            inn=params.get("inn", [""])[0],
            ogrn=params.get("ogrn", [None])[0],
            short_name=name or "—",
        )
    return Customer(inn="", short_name="—")


def _card_date(block: Node, label: str) -> tuple[datetime | None, date | None]:
    for node in block.css(".data-block__title"):
        if _collapse(node.text()).rstrip(":") != label:
            continue
        value = node.next
        while value is not None and value.tag == "-text":
            value = value.next
        if value is None or "data-block__value" not in value.attributes.get("class", "").split():
            break
        raw = _collapse(value.text())
        for pattern, date_only in (("%d.%m.%Y", True), ("%d.%m.%Y %H:%M", False)):
            try:
                parsed = datetime.strptime(raw, pattern)
            except ValueError:
                continue
            return (None, parsed.date()) if date_only else (parsed, None)
        break
    return None, None


def _parse_search_block(block: Node) -> Tender | None:
    link = block.css_first(".registry-entry__header-mid__number a")
    if not link:
        return None
    canonical = public_eis_url(link.attributes.get("href"))
    if not canonical:
        return None
    number = parse_qs(urlsplit(canonical).query).get("regNumber", [""])[0]
    if not is_notice_number(number):
        return None
    header = _first_text(block, ".registry-entry__header-top__title") or ""
    law = _law_from_header(header)
    title = _first_text(block, ".registry-entry__body-value")
    if not title or not law or (law == LawType.FZ_223) != (len(number) == 11):
        return None

    url_xml = f"{_EIS_BASE}{_VIEW_XML}?regNumber={number}"
    if law == LawType.FZ_223:
        links = [
            print_links_223(anchor.attributes.get("href"), number)
            for anchor in block.css(".registry-entry__header-top__icon a[href], a[href*='/notice223/printForm/']")
        ]
        verified = next((link_pair for link_pair in links if link_pair), None)
        canonical, url_xml = verified if verified else (canonical, None)
    published, published_day = _card_date(block, "Размещено")
    deadline, deadline_day = _card_date(block, "Окончание подачи заявок")
    return Tender(
        reg_number=number,
        law_type=law,
        title=title,
        customer=_customer_from_card(block),
        price_initial_rub=_parse_price(_first_text(block, ".price-block__value") or _first_text(block, ".price-block") or ""),
        platform=Platform.OTHER,
        status=_status_from_ru(_first_text(block, ".registry-entry__header-mid__title") or ""),
        publish_date=published,
        publish_date_only=published_day,
        applications_deadline=deadline,
        applications_deadline_date=deadline_day,
        url_eis=canonical,
        url_xml=url_xml,
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


def _first_text(tree: HTMLParser | Node, selector: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    text = _collapse(node.text())
    return text or None


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _law_from_header(header: str) -> LawType | None:
    if "223" in header:
        return LawType.FZ_223
    if "615" in header:
        return LawType.PP_615
    if "44" in header:
        return LawType.FZ_44
    return None


def _status_from_ru(raw: str) -> TenderStatus:
    s = raw.casefold().replace("ё", "е")
    if "отмен" in s:
        return TenderStatus.CANCELLED
    if "определение поставщика завершено" in s or "закупка завершена" in s or s == "завершена":
        return TenderStatus.COMPLETED
    if "работа комисс" in s or "рассмотрение заявок" in s or "подведение итогов" in s:
        return TenderStatus.IN_PROGRESS
    if "подача заявок завершен" in s or "прием заявок завершен" in s:
        return TenderStatus.APPLICATIONS_CLOSED
    if "подача заявок" in s or "прием заявок" in s:
        return TenderStatus.APPLICATIONS_OPEN
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
