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
from datetime import UTC, date, datetime
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

_NS_RE = re.compile(r"\{(.*?)\}")



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


def _find_in_container(
    root: ET.Element, container_name: str, *names: str
) -> ET.Element | None:
    """Найти элемент с любым из `names` только внутри первого контейнера `container_name`.

    Глобальный `_find_first` ищет по всему дереву и может вернуть
    `endDate` из раздела исполнения контракта вместо `endDT` из collectingInfo.
    """
    target = set(names)
    for container in root.iter():
        if _q(container.tag) == container_name:
            for child in container.iter():
                if _q(child.tag) in target and child is not container:
                    return child
            break
    return None


def _has_ancestor(
    el: ET.Element, parent_map: dict[int, ET.Element], name: str
) -> bool:
    """Проверить, есть ли у элемента предок с локальным именем `name`."""
    cur = el
    while True:
        cur = parent_map.get(id(cur))
        if cur is None:
            return False
        if _q(cur.tag) == name:
            return True


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


def _collect_timezone(root: ET.Element) -> str | None:
    """Извлечь часовой пояс из первой найденной даты со смещением."""
    for el in root.iter():
        val = (el.text or "").strip()
        m = re.search(r"([+-]\d{2}:\d{2})$", val)
        if m:
            return m.group(1)
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

    Исправления относительно v0.2.0:
    * applications_deadline из collectingInfo/endDT (или biddingDate),
      не через глобальный _find_first("endDate", ...).
    * contract_execution_end_date — календарная дата (date, YYYY-MM-DD), отдельное поле.
    * smp_only = None, если не найдено (не false).
    * parse_warnings, verified_fields, *_raw, applications_timezone.
    """
    law_type = _detect_law_type(root)

    parent_map: dict[int, ET.Element] = {}
    for p_el in root.iter():
        for c_el in p_el:
            parent_map[id(c_el)] = p_el

    parse_warnings: list[str] = []
    verified_fields: list[str] = []

    title = (
        _ftext(_find_first(root, "purchaseObjectInfo", "purchaseObjectInfo")) or "—"
    )
    verified_fields.extend(["reg_number", "law_type", "title"])

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

    publish_date_raw = _ftext(
        _find_first(root, "publishDate", "publicationDateTime", "docDate", "plannedPublishDate")
    )
    publish_date = _parse_dt(publish_date_raw)
    if publish_date_raw:
        verified_fields.append("publish_date")
    if publish_date_raw and not publish_date:
        parse_warnings.append(f"publish_date не распознан: {publish_date_raw}")

    apps_deadline_raw: str | None = None
    apps_deadline: datetime | None = None
    apps_timezone: str | None = None

    collecting_end_dt = _find_in_container(root, "collectingInfo", "endDT")
    if collecting_end_dt is not None:
        apps_deadline_raw = _ftext(collecting_end_dt)
    else:
        bidding_date = _ftext(_find_first(root, "biddingDate"))
        if bidding_date:
            apps_deadline_raw = bidding_date
            parse_warnings.append(
                "applications_deadline взят из biddingDate (collectingInfo/endDT не найден)"
            )

    if apps_deadline_raw:
        apps_deadline = _parse_dt(apps_deadline_raw)
        verified_fields.append("applications_deadline")
        # Extract timezone from the applications_deadline value itself (+07:00 / +03:00)
        m = re.search(r"([+-])(\d{2}):?(\d{2})$", apps_deadline_raw)
        if apps_deadline and m:
            apps_timezone = m.group(0)
        if not apps_deadline:
            parse_warnings.append(f"applications_deadline не распознан: {apps_deadline_raw}")
    else:
        parse_warnings.append(
            "applications_deadline не найден в XML (collectingInfo/endDT или biddingDate absent)"
        )

    # contract_execution_end_date — календарная дата (date, YYYY-MM-DD).
    # endDate берём только из раздела исполнения контракта:
    # contractConditionsInfo / contractExecutionPaymentPlan / ... /
    # notRelativeTermsInfo / endDate.
    # Правило "любой endDate вне collectingInfo" НЕ используется, потому что
    # endDate встречается также в этапах, гарантиях, обеспечении и финансировании.
    # Кандидатов нет — null + предупреждение. Несколько кандидатов с разными
    # значениями — путь неоднозначен → null + предупреждение (не выбираем первое
    # попавшееся). Значения совпадают — значение однозначно, извлекаем только дату
    # (смещение часового пояса +03:00 — не время исполнения).
    contract_exec_raw: str | None = None
    contract_exec_end_date: date | None = None
    contract_exec_end_date_raw: str | None = None

    contract_exec_candidates: list[ET.Element] = []
    for el in root.iter():
        if _q(el.tag) != "endDate":
            continue
        parent = parent_map.get(id(el))
        if parent is None or _q(parent.tag) != "notRelativeTermsInfo":
            continue
        if not _has_ancestor(el, parent_map, "contractExecutionPaymentPlan"):
            continue
        contract_exec_candidates.append(el)

    if not contract_exec_candidates:
        parse_warnings.append(
            "contract_execution_end_date не найден: "
            "contractConditionsInfo/contractExecutionPaymentPlan/"
            ".../notRelativeTermsInfo/endDate отсутствует"
        )
    else:
        raw_values = [(_ftext(el) or "").strip() for el in contract_exec_candidates]
        if len(set(raw_values)) != 1:
            parse_warnings.append(
                "contract_execution_end_date: несколько endDate в разделе исполнения "
                "контракта с разными значениями, не выбирать первое — вернуть null"
            )
        else:
            contract_exec_raw = raw_values[0]
            contract_exec_end_date_raw = contract_exec_raw
            if contract_exec_raw:
                m = re.match(r"^(\d{4}-\d{2}-\d{2})", contract_exec_raw)
                if m:
                    contract_exec_end_date = date.fromisoformat(m.group(1))
                    verified_fields.append("contract_execution_end_date")
                else:
                    parse_warnings.append(
                        f"contract_execution_end_date не распознан: {contract_exec_raw}"
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
    if price_initial is not None:
        verified_fields.append("price_initial_rub")

    smp_only_node = _find_first(root, "subjectsRF", "smallBusinessRequirement", "isSmallBusiness")
    smp_text = _ftext(smp_only_node)
    smp_only: bool | None = None
    if smp_text is not None:
        smp_only = smp_text.lower() in {"true", "1", "y"}
        verified_fields.append("smp_only")
    else:
        parse_warnings.append("smp_only не найден в XML, установлен None")

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
        applications_deadline_raw=apps_deadline_raw,
        applications_timezone=apps_timezone,
        contract_execution_end_date=contract_exec_end_date,
        contract_execution_end_date_raw=contract_exec_end_date_raw,
        auction_date=auction_date,
        platform=Platform.OTHER,
        status=TenderStatus.UNKNOWN,
        smp_only=smp_only,
        documents=documents,
        url_eis=url_eis,
        url_xml=url_xml,
        fetched_at=datetime.now(UTC),
        source_provider="html_fallback",
        parse_warnings=parse_warnings,
        verified_fields=verified_fields,
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