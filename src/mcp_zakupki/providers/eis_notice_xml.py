"""Разбор XML текущей редакции извещений ЕИС по 44-ФЗ и 223-ФЗ."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urljoin, urlsplit

from ..errors import ParseError
from ..schemas import Customer, Document, LawType, Platform, Tender
from .eis_regions import customer_region
from .html_fallback import _BASE, _guess_file_type, _parse_decimal

_223_PRINT_PATH = "/epz/order/notice/notice223/printForm/view.html"
_223_XML_PATH = "/epz/order/notice/notice223/printForm/viewXml.html"
_GUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
_NOTICE_NUMBER = re.compile(r"[0-9]{11}|[0-9]{19,21}")


def is_notice_number(value: str) -> bool:
    return _NOTICE_NUMBER.fullmatch(value) is not None


def public_eis_url(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        url = urljoin(_BASE, raw)
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme == "https" and parts.netloc == "zakupki.gov.ru":
        return url
    return None


def print_links_223(raw: str | None, reg_number: str) -> tuple[str, str] | None:
    url = public_eis_url(raw)
    if not url:
        return None
    parts = urlsplit(url)
    if parts.path != _223_PRINT_PATH:
        return None
    params = parse_qs(parts.query)
    guids = params.get("purchaseNoticeGuid", [])
    if (
        params.get("purchaseNoticeNumber") != [reg_number]
        or len(guids) != 1
        or not _GUID_RE.fullmatch(guids[0])
    ):
        return None
    query = f"purchaseNoticeGuid={guids[0]}&purchaseNoticeNumber={reg_number}"
    return (f"{_BASE}{_223_PRINT_PATH}?{query}", f"{_BASE}{_223_XML_PATH}?{query}")


def _node(root: ET.Element, path: str) -> ET.Element | None:
    return root.find("/".join(f"{{*}}{part}" for part in path.split("/")))


def _nodes(root: ET.Element, path: str) -> list[ET.Element]:
    return root.findall("/".join(f"{{*}}{part}" for part in path.split("/")))


def _text(root: ET.Element, *paths: str) -> str | None:
    for path in paths:
        node = _node(root, path)
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    return None


def source_datetime(raw: str | None) -> tuple[datetime | None, date | None]:
    if not raw:
        return None, None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})?", raw):
        try:
            return None, date.fromisoformat(raw[:10])
        except ValueError:
            return None, None
    try:
        return datetime.fromisoformat(raw), None
    except ValueError:
        return None, None


def _integer(raw: str | None) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _documents(root: ET.Element, path: str, *, enabled: bool) -> list[Document]:
    if not enabled:
        return []
    documents = []
    seen = set()
    for node in _nodes(root, path):
        name = _text(node, "fileName", "docDescription")
        url = public_eis_url(_text(node, "url", "downloadUrl"))
        if not name or not url or url in seen:
            continue
        seen.add(url)
        size = _integer(_text(node, "fileSize", "size"))
        documents.append(Document(title=name, url=url, file_type=_guess_file_type(name), size_bytes=size))
    return documents


def _classification(nodes: list[ET.Element]) -> list[tuple[str, str | None]]:
    values: dict[str, str | None] = {}
    for node in nodes:
        code = _text(node, "code")
        if code:
            values.setdefault(code, _text(node, "name"))
    return list(values.items())


def _customer_44(root: ET.Element) -> Customer:
    customers = _nodes(root, "notificationInfo/customerRequirementsInfo/customerRequirementInfo/customer")
    requirements = customers[0] if len(customers) == 1 else None
    responsible_is_customer = _text(root, "purchaseResponsibleInfo/responsibleRole") == "CU"
    responsible = _node(root, "purchaseResponsibleInfo/responsibleOrgInfo")
    actual = requirements if requirements is not None else root

    # Реквизиты размещающей организации допустимы только для того же заказчика.
    may_enrich = responsible is not None and responsible_is_customer and (
        not customers or (requirements is not None and _text(requirements, "regNum") == _text(responsible, "regNum"))
    )
    if may_enrich and responsible is not None:
        return _customer(responsible, alternate=actual)
    return _customer(actual)


def _customer(root: ET.Element, alternate: ET.Element | None = None) -> Customer:
    def value(*paths: str) -> str | None:
        return _text(root, *paths) or (_text(alternate, *paths) if alternate is not None else None)

    address = value("legalAddress", "factAddress", "factualAddress", "postAddress")
    region_code, region_name = customer_region(value("region"), address)
    return Customer(
        inn=value("INN", "inn") or "",
        ogrn=value("OGRN", "ogrn"),
        short_name=value("shortName", "fullName") or "—",
        full_name=value("fullName"),
        address=address,
        region_code=region_code,
        region_name=region_name,
    )


def _detail_dates(
    published: str | None,
    deadline: str | None,
    revision: int | None,
    summary: Tender,
) -> dict[str, datetime | date | None]:
    first_publish, first_day = source_datetime(published)
    if (revision is not None and revision > 1) or (
        first_publish is not None and summary.publish_date_only is not None
        and first_publish.date() != summary.publish_date_only
    ):
        first_publish, first_day = summary.publish_date, summary.publish_date_only
    close, close_day = source_datetime(deadline)
    return {
        "publish_date": first_publish or summary.publish_date,
        "publish_date_only": None if first_publish or summary.publish_date else first_day or summary.publish_date_only,
        "applications_deadline": close or summary.applications_deadline,
        "applications_deadline_date": None if close or summary.applications_deadline else close_day or summary.applications_deadline_date,
    }


def _tender_44(root: ET.Element, summary: Tender, *, include_documents: bool) -> Tender:
    number = _text(root, "commonInfo/purchaseNumber", "purchaseNumber")
    name = _text(root, "commonInfo/purchaseObjectInfo", "purchaseObjectInfo")
    if number != summary.reg_number or not name:
        raise ParseError("ЕИС вернула XML без совпадающего номера и наименования извещения.")

    base = "notificationInfo"
    price = f"{base}/contractConditionsInfo/maxPriceInfo"
    collecting = f"{base}/procedureInfo/collectingInfo"
    deadline = _text(root, f"{collecting}/endDT", f"{collecting}/endDate", "applicationInfo/applicationDeadline")
    dates = _detail_dates(
        _text(root, "commonInfo/publishDTInEIS", "commonInfo/publishDate", "publishDate"),
        deadline,
        _integer(_text(root, "versionNumber")),
        summary,
    )
    customer = _customer_44(root)
    if customer.short_name == "—":
        customer = summary.customer
    if not customer.region_code and summary.customer.region_code:
        customer = customer.model_copy(update={
            "region_code": summary.customer.region_code,
            "region_name": summary.customer.region_name,
        })
    auction_date, _ = source_datetime(_text(root, f"{base}/procedureInfo/biddingDT", "auctionDate"))
    url = public_eis_url(_text(root, "commonInfo/href")) or summary.url_eis
    positions = f"{base}/purchaseObjectsInfo/notDrugPurchaseObjectsInfo/purchaseObject"
    ktru = _classification(_nodes(root, f"{positions}/KTRU"))
    okpd = _classification(_nodes(root, f"{positions}/OKPD2"))
    currency = _text(root, f"{price}/currency/code") or summary.currency
    price_value = _parse_decimal(_text(root, f"{price}/maxPrice", "maxPrice"))
    return summary.model_copy(update={
        "title": name,
        "customer": customer,
        "url_eis": url,
        "price_initial_rub": None if currency != "RUB" else (
            price_value if price_value is not None else summary.price_initial_rub
        ),
        "currency": currency,
        "okpd2_codes": [code for code, _ in okpd],
        "okpd2_names": [name for _, name in okpd if name],
        "ktru_codes": [code for code, _ in ktru],
        "auction_date": auction_date,
        "platform": Platform.RTS_TENDER if _text(root, "commonInfo/ETP/code") == "ETP_RTS" else summary.platform,
        "documents": _documents(root, "attachmentsInfo/attachmentInfo", enabled=include_documents),
        "fetched_at": datetime.now(UTC),
        **dates,
    })


def _close_223(data: ET.Element) -> str | None:
    deadline = _text(data, "submissionCloseDateTime", "placingProcedure/submissionCloseDateTime")
    value, _ = source_datetime(deadline)
    customer_offset = _integer(_text(data, "customer/mainInfo/timeZone/offset"))
    placer_offset = _integer(_text(data, "placer/mainInfo/timeZone/offset"))
    # В печатной форме 223-ФЗ время окончания локальное. При разных или
    # отсутствующих зонах сохраняем значение XML без приписанной зоны.
    if (
        value is not None and value.tzinfo is None
        and customer_offset is not None and customer_offset == placer_offset
        and -12 <= customer_offset + 3 <= 14
    ):
        return value.replace(tzinfo=timezone(timedelta(hours=customer_offset + 3))).isoformat()
    return deadline


def _tender_223(root: ET.Element, summary: Tender, *, include_documents: bool) -> Tender:
    notice_type = root.tag.rsplit("}", 1)[-1]
    items = _nodes(root, f"body/item/{notice_type}Data")
    data = next(
        (item for item in items if _text(item, "registrationNumber") == summary.reg_number),
        None,
    )
    if data is None or not (name := _text(data, "name")):
        raise ParseError("ЕИС вернула XML 223-ФЗ без совпадающего номера и наименования извещения.")

    customer_node = _node(data, "customer/mainInfo")
    customer = _customer(customer_node) if customer_node is not None else summary.customer
    dates = _detail_dates(
        _text(data, "publicationDateTime"),
        _close_223(data),
        _integer(_text(data, "version")),
        summary,
    )
    lots = _nodes(data, "lots/lot/lotData")
    price, currency = _lot_price(lots, summary)
    okpd = _classification([node for lot in lots for node in _nodes(lot, "lotItems/lotItem/okpd2")])
    platform = _text(data, "electronicPlaceInfo/name")
    return summary.model_copy(update={
        "title": name,
        "customer": customer,
        "price_initial_rub": price,
        "currency": currency,
        "okpd2_codes": [code for code, _ in okpd],
        "okpd2_names": [name for _, name in okpd if name],
        "platform": Platform.RTS_TENDER if platform and platform.casefold() == "ртс-тендер" else summary.platform,
        "documents": _documents(data, "attachments/document", enabled=include_documents),
        "fetched_at": datetime.now(UTC),
        **dates,
    })


def _lot_price(lots: list[ET.Element], summary: Tender) -> tuple[Decimal | None, str]:
    if not lots:
        return summary.price_initial_rub, summary.currency
    totals = [_parse_decimal(_text(lot, "initialSum")) for lot in lots]
    currencies = {_text(lot, "currency/code") for lot in lots}
    if len(currencies) == 1 and None not in currencies and all(
        total is not None and total.is_finite() for total in totals
    ):
        currency = next(iter(currencies))
        amount = sum((total for total in totals if total is not None), Decimal(0))
        return (amount if currency == "RUB" else None), currency or summary.currency
    return None, summary.currency


def parse_notice_xml(text: str, summary: Tender, *, include_documents: bool) -> Tender:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParseError("ЕИС вернула невалидный XML печатной формы извещения.") from exc
    if summary.law_type == LawType.FZ_223:
        return _tender_223(root, summary, include_documents=include_documents)
    return _tender_44(root, summary, include_documents=include_documents)
