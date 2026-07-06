"""Провайдер ГосПлан API v2 (`v2.gosplan.info`).

Документация: `https://wiki.gosplan.info`. На 2026-04 поэтапный roll-out:
    Beta — `v2beta.gosplan.info` — 44-ФЗ
    Pre1 — `v2pre1.gosplan.info` — 615-ПП
    Pre2 — `v2pre2.gosplan.info` — 223-ФЗ
Sandbox без регистрации: `fz44test.gosplan.info`.

Аутентификация: API-Key (заменил JWT в v1) — заголовок `X-API-Key: <token>`.
Без ключа провайдер кидает `AuthFailedError`, чтобы резолвер
переключился на следующий канал в цепочке.

В Phase 0–1 — заглушка с реальным HTTP-вызовом, но без полного маппинга
нестандартных полей ГосПлан-схемы (она различается по версиям). Полная
реализация — Phase 1.x (после получения тестового ключа в спринте).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..config import AppConfig
from ..errors import AuthFailedError, NotFoundError, ParseError
from ..schemas import (
    Customer,
    LawType,
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


class GosplanProvider(BaseProvider):
    """REST-клиент ГосПлан API v2."""

    def __init__(self, config: AppConfig, *, http: HttpAdapter | None = None) -> None:
        self._config = config
        self._key = config.providers.gosplan_key
        self._base = config.providers.gosplan_base.rstrip("/")
        self._http = http or HttpAdapter(config)
        self._owns_http = http is None

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="gosplan",
            label="ГосПлан API v2",
            capabilities=ProviderCapability.SEARCH | ProviderCapability.TENDER_DETAILS,
            requires_auth=True,
        )

    @property
    def is_configured(self) -> bool:
        return self._key is not None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _auth_headers(self) -> dict[str, str]:
        if self._key is None:
            raise AuthFailedError(
                "ГосПлан: API-ключ не задан. Установите MCP_ZAKUPKI_GOSPLAN_KEY "
                "или удалите 'gosplan' из MCP_ZAKUPKI_PROVIDERS.",
                details={"provider": "gosplan"},
            )
        return {"X-API-Key": self._key}

    async def search_tenders(self, filter_: TenderSearchFilter) -> SearchResult:
        headers = self._auth_headers()
        params = _build_search_params(filter_)
        try:
            payload = await self._http.get_json(
                f"{self._base}/v2/notifications",
                provider_name=self.info.label,
                params=params,
                headers=headers,
            )
        except (NotFoundError, AuthFailedError):
            raise
        if not isinstance(payload, dict):
            raise ParseError(
                "ГосПлан: ожидался JSON-объект на /v2/notifications.",
                details={"provider": "gosplan"},
            )
        items = payload.get("items") or payload.get("data") or []
        tenders = [_parse_tender_summary(it) for it in items if isinstance(it, dict)]
        return SearchResult(
            query_echo=filter_.model_dump(mode="json"),
            total_estimated=_safe_int(payload.get("total")),
            page_size=len(tenders),
            tenders=tenders,
            search_meta=SearchMeta(took_ms=0, source="gosplan", cache_hit=False),
        )

    async def get_tender(
        self,
        reg_number: str,
        *,
        include_documents: bool = True,
        include_protocols: bool = False,
    ) -> Tender:
        headers = self._auth_headers()
        try:
            payload = await self._http.get_json(
                f"{self._base}/v2/notifications/{reg_number}",
                provider_name=self.info.label,
                headers=headers,
                expected_404_to_not_found=True,
            )
        except NotFoundError:
            raise
        if not isinstance(payload, dict) or not payload:
            raise NotFoundError(
                f"ГосПлан: тендер {reg_number} не найден.",
                details={"reg_number": reg_number, "provider": "gosplan"},
            )
        return _parse_tender_full(payload, reg_number)


# ---- helpers --------------------------------------------------------------


def _build_search_params(filter_: TenderSearchFilter) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": filter_.limit}
    if filter_.query:
        params["q"] = filter_.query
    if filter_.okpd2_codes:
        params["okpd"] = ",".join(filter_.okpd2_codes)
    if filter_.regions:
        params["region"] = ",".join(filter_.regions)
    if filter_.customer_inn:
        params["customer_inn"] = filter_.customer_inn
    if filter_.price_min_rub is not None:
        params["price_min"] = filter_.price_min_rub
    if filter_.price_max_rub is not None:
        params["price_max"] = filter_.price_max_rub
    if filter_.smp_only:
        params["smp"] = 1
    if filter_.publish_date_from:
        params["publish_from"] = filter_.publish_date_from
    if filter_.publish_date_to:
        params["publish_to"] = filter_.publish_date_to
    if filter_.law_type:
        params["fz"] = ",".join(_strip_fz(_as_str(lt)) for lt in filter_.law_type)
    if filter_.next_page_token:
        params["page"] = filter_.next_page_token
    return params


def _strip_fz(v: str) -> str:
    return v.replace("-fz", "").replace("-pp", "")


def _as_str(v: Any) -> str:
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)


def _safe_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _parse_tender_summary(item: dict[str, Any]) -> Tender:
    reg_number = str(item.get("reg") or item.get("reg_number") or item.get("id") or "")
    return Tender(
        reg_number=reg_number,
        law_type=_law_from(item.get("fz") or item.get("law")),
        title=str(item.get("title") or item.get("name") or "—"),
        okpd2_codes=_csv(item.get("okpd")),
        customer=Customer(
            inn=str(item.get("customer_inn") or ""),
            short_name=str(item.get("customer_name") or "—"),
            region_code=_str_or_none(item.get("region")),
        ),
        price_initial_rub=None,
        publish_date=None,
        applications_deadline=None,
        platform=Platform.OTHER,
        status=TenderStatus.UNKNOWN,
        smp_only=bool(item.get("smp")),
        url_eis=str(
            item.get("url")
            or f"https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={reg_number}"
        ),
        url_xml=f"https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html?regNumber={reg_number}",
        fetched_at=datetime.now(UTC),
        source_provider="gosplan",
    )


def _parse_tender_full(payload: dict[str, Any], reg_number: str) -> Tender:
    customer_block = payload.get("customer") or {}
    return Tender(
        reg_number=reg_number,
        law_type=_law_from(payload.get("fz") or payload.get("law")),
        title=str(payload.get("title") or payload.get("name") or "—"),
        okpd2_codes=_csv(payload.get("okpd")),
        customer=Customer(
            inn=str(customer_block.get("inn") or ""),
            ogrn=customer_block.get("ogrn"),
            short_name=str(customer_block.get("short_name") or customer_block.get("name") or "—"),
            region_code=_str_or_none(customer_block.get("region")),
            address=customer_block.get("address"),
        ),
        price_initial_rub=None,
        publish_date=None,
        applications_deadline=None,
        platform=Platform.OTHER,
        status=TenderStatus.UNKNOWN,
        smp_only=bool(payload.get("smp")),
        url_eis=str(
            payload.get("url")
            or f"https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={reg_number}"
        ),
        url_xml=f"https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html?regNumber={reg_number}",
        fetched_at=datetime.now(UTC),
        source_provider="gosplan",
    )


def _law_from(v: Any) -> LawType:
    s = str(v or "").lower()
    if "223" in s:
        return LawType.FZ_223
    if "615" in s:
        return LawType.PP_615
    return LawType.FZ_44


def _csv(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [s.strip() for s in str(v).split(",") if s.strip()]


def _str_or_none(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


__all__ = ["GosplanProvider"]
