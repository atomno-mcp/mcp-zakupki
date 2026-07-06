"""HTML-fallback: парсинг публичных страниц `zakupki.gov.ru`.

Используется как последний рубеж — без ключей DaMIA / ГосПлан / navodki.
Возможности:

* `get_tender(reg_number)` — через `viewXml.html?regNumber=...` (открытый
  XML-вид извещения, доступен анонимно). Парсим с помощью stdlib
  ElementTree + точечный fallback на selectolax если HTML.
* `search_tenders(filter_)` — через `extendedsearch/results.html?searchString=...`.
  В Phase 0 — заглушка с понятным сообщением; полный парсинг страницы
  поиска перенесён в Phase 1.x.

Ограничения (см. SPEC §7.5.5):
* Антибот-защита (CloudFlare WAF / iframe-капча); может быть блокировка по IP.
* Структура HTML меняется без анонса.
* Нет batch-запросов.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from ..config import AppConfig
from ..errors import NotFoundError, ParseError
from ..schemas import (
    Customer,
    Document,
    FileType,
    LawType,
    Platform,
    SearchResult,
    Tender,
    TenderStatus,
)
from .base import BaseProvider, ProviderCapability, ProviderInfo
from .http_client import HttpAdapter

if TYPE_CHECKING:
    from ..schemas import TenderSearchFilter

logger = logging.getLogger(__name__)


_BASE = "https://zakupki.gov.ru"
_VIEW_XML = "/epz/order/notice/printForm/viewXml.html"
_VIEW_HTML = "/epz/order/notice/printForm/view.html"


class HtmlFallbackProvider(BaseProvider):
    """Парсинг публичных страниц ЕИС без ключей."""

    def __init__(self, config: AppConfig, *, http: HttpAdapter | None = None) -> None:
        self._config = config
        self._http = http or HttpAdapter(config)
        self._owns_http = http is None

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="html_fallback",
            label="HTML-fallback (zakupki.gov.ru)",
            capabilities=ProviderCapability.TENDER_DETAILS,
            requires_auth=False,
        )

    @property
    def is_configured(self) -> bool:
        return self._config.allow_html_scraping

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search_tenders(self, filter_: TenderSearchFilter) -> SearchResult:
        # Парсинг страницы поиска `extendedsearch/results.html?searchString=...`
        # реализуется в Phase 1.x. Открытый XML отдаётся только по reg_number,
        # поэтому без точного номера html_fallback не умеет search.
        raise NotImplementedError(
            "HTML-fallback не поддерживает search_tenders в Phase 0. "
            "Используйте DaMIA / ГосПлан / navodki, или укажите reg_number "
            "и вызовите get_tender."
        )

    async def get_tender(
        self,
        reg_number: str,
        *,
        include_documents: bool = True,
        include_protocols: bool = False,
    ) -> Tender:
        url = f"{_BASE}{_VIEW_XML}"
        params = {"regNumber": reg_number}
        text = await self._http.get_text(
            url,
            provider_name=self.info.label,
            params=params,
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
                f"HTML-fallback: невалидный XML на {url}?regNumber={reg_number}.",
                details={"reg_number": reg_number},
            ) from exc

        return _xml_to_tender(
            root,
            reg_number,
            url_xml=f"{url}?regNumber={reg_number}",
            url_eis=f"{_BASE}{_VIEW_HTML}?regNumber={reg_number}",
            include_documents=include_documents,
        )


# ---- Минимальный XML-парсинг ---------------------------------------------

# ЕИС возвращает XML с разными namespace'ами; на v0 оперируем по
# local-name через утилиту `_q`. Полная нормализация под десятки
# `subsystemType` (PRIZ/RGK/RKPO/...) — Phase 1.x.

_NS_RE = re.compile(r"\{[^}]+\}")


def _q(tag: str) -> str:
    """Strip XML namespace для упрощённого матчинга."""
    return _NS_RE.sub("", tag)


def _ftext(elem: ET.Element | None) -> str | None:
    if elem is None:
        return None
    text = (elem.text or "").strip()
    return text or None


def _find_first(root: ET.Element, *names: str) -> ET.Element | None:
    """Найти первый элемент с любым из переданных local-name (рекурсивно)."""
    target = set(names)
    for el in root.iter():
        if _q(el.tag) in target:
            return el
    return None


def _find_all(root: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in root.iter() if _q(el.tag) == name]


def _detect_law_type(root: ET.Element) -> LawType:
    """Определить 44-ФЗ vs 223-ФЗ vs 615-ПП по namespace или контенту."""
    for el in root.iter():
        tag = _q(el.tag)
        if tag in {"export44", "fcsNotificationEF", "fcsNotificationEF2020"}:
            return LawType.FZ_44
        if tag in {"export223", "purchaseNotice", "ntfPurchaseNotice"}:
            return LawType.FZ_223
        if tag.startswith("rkpo") or "615" in tag:
            return LawType.PP_615
    # Fallback по namespace в корне
    if isinstance(root.tag, str):
        if "fcs" in root.tag.lower() or "44fz" in root.tag.lower():
            return LawType.FZ_44
        if "223" in root.tag.lower():
            return LawType.FZ_223
    return LawType.FZ_44


def _parse_decimal(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    s = raw.replace(" ", "").replace(",", ".")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    cleaned = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _xml_to_tender(
    root: ET.Element,
    reg_number: str,
    *,
    url_xml: str,
    url_eis: str,
    include_documents: bool,
) -> Tender:
    """Минимальная нормализация ЕИС-XML в `Tender`-модель.

    Делается best-effort: если поле отсутствует — None / пустой default.
    Полный маппинг по 44-ФЗ / 223-ФЗ / 615-ПП — задача Phase 1.x.
    """
    law_type = _detect_law_type(root)

    title = (
        _ftext(_find_first(root, "purchaseObjectInfo", "purchaseObjectInfo")) or "—"
    )
    title_full = _ftext(_find_first(root, "purchaseObjectInfoFull")) or None

    customer_inn = _ftext(_find_first(root, "INN", "inn"))
    customer_ogrn = _ftext(_find_first(root, "OGRN", "ogrn"))
    customer_short_name = _ftext(
        _find_first(
            root,
            "shortName",
            "fullName",
            "customerShortName",
            "customerFullName",
        )
    ) or "—"
    region_code = _ftext(_find_first(root, "code", "regionCode"))
    address = _ftext(_find_first(root, "factualAddress", "factAddress", "postAddress"))

    publish_date = _parse_dt(_ftext(_find_first(root, "publishDate", "publicationDateTime")))
    apps_deadline = _parse_dt(
        _ftext(_find_first(root, "endDate", "applicationDeadline", "endDateTime"))
    )
    auction_date = _parse_dt(_ftext(_find_first(root, "auctionDate", "auctionStartDateTime")))

    price_node = _find_first(
        root,
        "maxPrice",
        "contractPrice",
        "initialContractPrice",
        "purchaseInitialPrice",
    )
    price_initial = _parse_decimal(_ftext(price_node))

    smp_only_node = _find_first(root, "subjectsRF", "smallBusinessRequirement", "isSmallBusiness")
    smp_only = (_ftext(smp_only_node) or "").lower() in {"true", "1", "y"}

    documents: list[Document] = []
    if include_documents:
        for doc in _find_all(root, "attachment") + _find_all(root, "attachmentInfo"):
            url = _ftext(_find_first(doc, "url", "downloadUrl"))
            name = _ftext(_find_first(doc, "fileName", "docDescription"))
            if url and name:
                documents.append(Document(title=name, url=url, file_type=_guess_file_type(name)))

    customer = Customer(
        inn=customer_inn or "",
        ogrn=customer_ogrn,
        short_name=customer_short_name,
        region_code=region_code,
        address=address,
    )

    return Tender(
        reg_number=reg_number,
        law_type=law_type,
        title=title,
        title_full=title_full,
        customer=customer,
        price_initial_rub=price_initial,
        publish_date=publish_date,
        applications_deadline=apps_deadline,
        auction_date=auction_date,
        platform=Platform.OTHER,
        status=TenderStatus.UNKNOWN,
        smp_only=smp_only,
        documents=documents,
        url_eis=url_eis,
        url_xml=url_xml,
        fetched_at=datetime.now(UTC),
        source_provider="html_fallback",
    )


def _guess_file_type(filename: str) -> FileType:
    name = filename.lower()
    for suffix, ft in (
        (".pdf", FileType.PDF),
        (".docx", FileType.DOCX),
        (".doc", FileType.DOC),
        (".xlsx", FileType.XLSX),
        (".xls", FileType.XLS),
        (".rtf", FileType.RTF),
        (".zip", FileType.ZIP),
    ):
        if name.endswith(suffix):
            return ft
    return FileType.OTHER


__all__: list[Any] = ["HtmlFallbackProvider"]
