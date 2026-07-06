"""Провайдер DaMIA API-Закупки (`api.damia.ru/zakupki`).

Документация всех методов — `https://damia.ru/apizakupki`. Тариф
per-request, бесплатный план «DaMIA-API-Старт» даёт ~100 запросов/мес.

Реализация Phase 0–1 — `search_tenders` (через `zsearch`) и
`get_tender` (через `zakupka`). История заказчика и поставщика
(`get_customer_history` / `get_supplier_stats`) — в Phase 1.x.

Аутентификация: API-ключ в query-параметре `key=<token>`. Без ключа
все запросы кидают `AuthFailedError`, чтобы резолвер мог переключиться
на следующего провайдера.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from ..config import AppConfig
from ..errors import AuthFailedError, NotFoundError, ParseError
from ..schemas import (
    Customer,
    Document,
    FileType,
    LawType,
    OrgHistorySummary,
    Platform,
    SearchMeta,
    SearchResult,
    Tender,
    TenderStatus,
)
from .base import BaseProvider, ProviderCapability, ProviderInfo
from .http_client import HttpAdapter

if TYPE_CHECKING:
    from ..schemas import TenderSearchFilter

logger = logging.getLogger(__name__)

DAMIA_BASE = "https://api.damia.ru/zakupki"


class DamiaProvider(BaseProvider):
    """REST-клиент DaMIA API-Закупки."""

    def __init__(self, config: AppConfig, *, http: HttpAdapter | None = None) -> None:
        self._config = config
        self._key = config.providers.damia_key
        self._http = http or HttpAdapter(config)
        self._owns_http = http is None

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="damia",
            label="DaMIA API-Закупки",
            capabilities=ProviderCapability.SEARCH
            | ProviderCapability.TENDER_DETAILS
            | ProviderCapability.CUSTOMER_HISTORY
            | ProviderCapability.SUPPLIER_STATS,
            requires_auth=True,
        )

    @property
    def is_configured(self) -> bool:
        return self._key is not None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _require_key(self) -> str:
        if self._key is None:
            raise AuthFailedError(
                "DaMIA: API-ключ не задан. Установите MCP_ZAKUPKI_DAMIA_KEY "
                "или удалите 'damia' из MCP_ZAKUPKI_PROVIDERS.",
                details={"provider": "damia"},
            )
        return self._key

    async def search_tenders(self, filter_: TenderSearchFilter) -> SearchResult:
        key = self._require_key()
        params = _build_search_params(filter_, key)
        payload = await self._http.get_json(
            f"{DAMIA_BASE}/zsearch",
            provider_name=self.info.label,
            params=params,
        )
        try:
            tenders = _parse_search_payload(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ParseError(
                "DaMIA вернул неожиданную структуру в /zsearch.",
                details={"provider": "damia"},
            ) from exc
        return SearchResult(
            query_echo=filter_.model_dump(mode="json"),
            total_estimated=_safe_int(payload.get("total")),
            page_size=len(tenders),
            tenders=tenders,
            search_meta=SearchMeta(took_ms=0, source="damia", cache_hit=False),
        )

    async def get_tender(
        self,
        reg_number: str,
        *,
        include_documents: bool = True,
        include_protocols: bool = False,
    ) -> Tender:
        key = self._require_key()
        try:
            payload = await self._http.get_json(
                f"{DAMIA_BASE}/zakupka",
                provider_name=self.info.label,
                params={"reg": reg_number, "key": key},
                expected_404_to_not_found=True,
            )
        except NotFoundError:
            raise
        if not payload or (isinstance(payload, dict) and payload.get("error")):
            raise NotFoundError(
                f"DaMIA: тендер {reg_number} не найден.",
                details={"reg_number": reg_number, "provider": "damia"},
            )
        try:
            return _parse_tender_payload(payload, reg_number)
        except (KeyError, TypeError, ValueError) as exc:
            raise ParseError(
                "DaMIA: невалидная структура карточки тендера.",
                details={"provider": "damia", "reg_number": reg_number},
            ) from exc

    async def get_customer_history(
        self,
        *,
        inn: str | None = None,
        ogrn: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> OrgHistorySummary:
        key = self._require_key()
        ident = inn or ogrn or ""
        if not ident:
            raise AuthFailedError(
                "DaMIA: для get_customer_history нужен ИНН или ОГРН.",
                details={"provider": "damia"},
            )
        params: dict[str, Any] = {"inn": ident, "key": key}
        if period_from:
            params["from"] = period_from
        if period_to:
            params["to"] = period_to
        payload = await self._http.get_json(
            f"{DAMIA_BASE}/customer",
            provider_name=self.info.label,
            params=params,
            expected_404_to_not_found=True,
        )
        try:
            return _parse_org_history_payload(
                payload,
                org_inn=ident,
                org_role="customer",
                period_from=period_from or "",
                period_to=period_to or "",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ParseError(
                "DaMIA: невалидная структура /customer.",
                details={"provider": "damia", "inn": ident},
            ) from exc

    async def get_supplier_stats(
        self,
        *,
        inn: str | None = None,
        ogrn: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> OrgHistorySummary:
        key = self._require_key()
        ident = inn or ogrn or ""
        if not ident:
            raise AuthFailedError(
                "DaMIA: для get_supplier_stats нужен ИНН или ОГРН.",
                details={"provider": "damia"},
            )
        params: dict[str, Any] = {"inn": ident, "type": "supplier", "key": key}
        if period_from:
            params["from"] = period_from
        if period_to:
            params["to"] = period_to
        payload = await self._http.get_json(
            f"{DAMIA_BASE}/contracts",
            provider_name=self.info.label,
            params=params,
            expected_404_to_not_found=True,
        )
        try:
            return _parse_org_history_payload(
                payload,
                org_inn=ident,
                org_role="supplier",
                period_from=period_from or "",
                period_to=period_to or "",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ParseError(
                "DaMIA: невалидная структура /contracts.",
                details={"provider": "damia", "inn": ident},
            ) from exc


# ---- helpers --------------------------------------------------------------


def _build_search_params(filter_: TenderSearchFilter, key: str) -> dict[str, Any]:
    params: dict[str, Any] = {"key": key, "limit": filter_.limit}
    if filter_.query:
        params["q"] = filter_.query
    if filter_.okpd2_codes:
        params["okpd"] = ",".join(filter_.okpd2_codes)
    if filter_.regions:
        params["region"] = ",".join(filter_.regions)
    if filter_.customer_inn:
        params["customer_inn"] = filter_.customer_inn
    if filter_.platform:
        # `Platform`-enum или строка (use_enum_values=True): нормализуем в str
        params["etp"] = ",".join(_as_str(p) for p in filter_.platform)
    if filter_.price_min_rub is not None:
        params["pricemin"] = filter_.price_min_rub
    if filter_.price_max_rub is not None:
        params["pricemax"] = filter_.price_max_rub
    if filter_.smp_only:
        params["smp"] = 1
    if filter_.publish_date_from:
        params["from"] = filter_.publish_date_from
    if filter_.publish_date_to:
        params["to"] = filter_.publish_date_to
    if filter_.law_type:
        params["fz"] = ",".join(_as_str(lt).replace("-fz", "").replace("-pp", "") for lt in filter_.law_type)
    if filter_.next_page_token:
        params["page"] = filter_.next_page_token
    return params


def _as_str(v: Any) -> str:
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)


def _safe_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _safe_dt(v: Any) -> datetime | None:
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_search_payload(payload: Any) -> list[Tender]:
    """DaMIA: ответ обычно {"items": [...], "total": N} либо просто список."""
    items: list[dict[str, Any]]
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("zakupki") or []
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    return [_parse_tender_summary(it) for it in items if isinstance(it, dict)]


def _parse_tender_summary(item: dict[str, Any]) -> Tender:
    reg_number = str(item.get("reg") or item.get("regNumber") or item.get("reestrNumber") or "")
    customer_inn = str(item.get("customer_inn") or item.get("zakazInn") or "")
    customer_name = str(item.get("customer_name") or item.get("zakazName") or "—")
    region_code = item.get("region") or item.get("regionCode")
    return Tender(
        reg_number=reg_number,
        law_type=_law_from_str(item.get("fz") or item.get("law")),
        title=str(item.get("title") or item.get("name") or "—"),
        okpd2_codes=_split_csv(item.get("okpd")),
        customer=Customer(
            inn=customer_inn,
            short_name=customer_name,
            region_code=str(region_code) if region_code is not None else None,
        ),
        price_initial_rub=_safe_decimal(item.get("price")),
        publish_date=_safe_dt(item.get("publish_date") or item.get("dtPublish")),
        applications_deadline=_safe_dt(item.get("end_date") or item.get("dtEnd")),
        platform=Platform.OTHER,
        status=_status_from_str(item.get("status")),
        smp_only=bool(item.get("smp")),
        url_eis=item.get("url") or f"https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={reg_number}",
        url_xml=f"https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html?regNumber={reg_number}",
        fetched_at=datetime.now(UTC),
        source_provider="damia",
    )


def _parse_tender_payload(payload: Any, reg_number: str) -> Tender:
    """Полная карточка из `/zakupka`. Структура в DaMIA — словарь верхнего уровня."""
    if not isinstance(payload, dict):
        raise ParseError(
            "DaMIA: ожидался JSON-объект для карточки тендера.",
            details={"provider": "damia", "reg_number": reg_number},
        )
    customer_block = payload.get("customer") or {}
    documents_block = payload.get("documents") or []
    return Tender(
        reg_number=reg_number,
        law_type=_law_from_str(payload.get("fz") or payload.get("law")),
        title=str(payload.get("title") or payload.get("name") or "—"),
        title_full=str(payload.get("title_full")) if payload.get("title_full") else None,
        okpd2_codes=_split_csv(payload.get("okpd")),
        customer=Customer(
            inn=str(customer_block.get("inn") or payload.get("customer_inn") or ""),
            ogrn=customer_block.get("ogrn"),
            short_name=str(customer_block.get("short_name") or customer_block.get("name") or "—"),
            full_name=customer_block.get("full_name"),
            region_code=str(customer_block.get("region")) if customer_block.get("region") else None,
            address=customer_block.get("address"),
        ),
        price_initial_rub=_safe_decimal(payload.get("price") or payload.get("priceInitial")),
        publish_date=_safe_dt(payload.get("publish_date")),
        applications_deadline=_safe_dt(payload.get("end_date")),
        platform=Platform.OTHER,
        status=_status_from_str(payload.get("status")),
        smp_only=bool(payload.get("smp")),
        documents=[
            Document(
                title=str(d.get("name") or d.get("title") or "—"),
                url=str(d.get("url") or ""),
                file_type=FileType.OTHER,
            )
            for d in documents_block
            if isinstance(d, dict) and d.get("url")
        ],
        url_eis=str(
            payload.get("url")
            or f"https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={reg_number}"
        ),
        url_xml=f"https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html?regNumber={reg_number}",
        fetched_at=datetime.now(UTC),
        source_provider="damia",
    )


def _parse_org_history_payload(
    payload: Any,
    *,
    org_inn: str,
    org_role: str,
    period_from: str,
    period_to: str,
) -> OrgHistorySummary:
    """Best-effort: DaMIA отдаёт {tenders_total, sum_price, top_okpd, ...}."""
    data = payload if isinstance(payload, dict) else {}
    return OrgHistorySummary(
        org_inn=org_inn,
        org_role=org_role,  # type: ignore[arg-type]
        short_name=str(data.get("name")) if data.get("name") else None,
        region_code=str(data.get("region")) if data.get("region") else None,
        period_from=period_from,
        period_to=period_to,
        tenders_total=_safe_int(data.get("tenders_total") or data.get("count")) or 0,
        tenders_completed=_safe_int(data.get("tenders_completed")) or 0,
        tenders_cancelled=_safe_int(data.get("tenders_cancelled")) or 0,
        total_volume_rub=_safe_decimal(data.get("sum_price") or data.get("volume")),
        avg_contract_rub=_safe_decimal(data.get("avg_price")),
        median_contract_rub=_safe_decimal(data.get("median_price")),
        smp_share_pct=_safe_float(data.get("smp_share_pct")),
        rnp_status=_rnp_from_payload(data),
        fetched_at=datetime.now(UTC),
        source_provider="damia",
    )


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _split_csv(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [s.strip() for s in str(v).split(",") if s.strip()]


def _law_from_str(v: Any) -> LawType:
    if v is None:
        return LawType.FZ_44
    s = str(v).lower().replace(" ", "")
    if "223" in s:
        return LawType.FZ_223
    if "615" in s:
        return LawType.PP_615
    return LawType.FZ_44


def _status_from_str(v: Any) -> TenderStatus:
    if v is None:
        return TenderStatus.UNKNOWN
    s = str(v).lower()
    if "open" in s or "applications_open" in s or "active" in s:
        return TenderStatus.APPLICATIONS_OPEN
    if "closed" in s:
        return TenderStatus.APPLICATIONS_CLOSED
    if "completed" in s or "finished" in s:
        return TenderStatus.COMPLETED
    if "cancel" in s:
        return TenderStatus.CANCELLED
    if "publish" in s:
        return TenderStatus.PUBLISHED
    return TenderStatus.UNKNOWN


def _rnp_from_payload(data: dict[str, Any]) -> Any:
    if "rnp" in data:
        v = data.get("rnp")
        if v in (True, "in", "in_rnp", 1):
            return "in_rnp"
        if v in (False, "clean", "ok", 0):
            return "clean"
    return "unknown"


__all__ = ["DamiaProvider"]
