"""Провайдер navodki.ru — REST-агрегатор поверх ЕИС.

Open-source клиент `webnitros/api-rest-navodki` на GitHub. Возможности:
- Все тендеры с правильным статусом.
- 44-ФЗ + 223-ФЗ.
- Free-тариф ограниченный.

Используется как третий fallback (после DaMIA и ГосПлан) или для
демонстраций без коммерческих ключей.

В Phase 0–1 — заглушка с реальным HTTP, без полного маппинга.
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

NAVODKI_BASE = "https://navodki.ru/api/v1"


class NavodkiProvider(BaseProvider):
    """REST-клиент navodki.ru."""

    def __init__(self, config: AppConfig, *, http: HttpAdapter | None = None) -> None:
        self._config = config
        self._key = config.providers.navodki_key
        self._http = http or HttpAdapter(config)
        self._owns_http = http is None

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="navodki",
            label="navodki.ru",
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
                "navodki.ru: API-ключ не задан. Установите MCP_ZAKUPKI_NAVODKI_KEY "
                "или удалите 'navodki' из MCP_ZAKUPKI_PROVIDERS.",
                details={"provider": "navodki"},
            )
        return {"Authorization": f"Bearer {self._key}"}

    async def search_tenders(self, filter_: TenderSearchFilter) -> SearchResult:
        headers = self._auth_headers()
        params = _build_search_params(filter_)
        payload = await self._http.get_json(
            f"{NAVODKI_BASE}/tenders/search",
            provider_name=self.info.label,
            params=params,
            headers=headers,
        )
        if not isinstance(payload, dict):
            raise ParseError(
                "navodki.ru: ожидался JSON-объект на /tenders/search.",
                details={"provider": "navodki"},
            )
        items = payload.get("items") or payload.get("data") or []
        tenders = [_parse_tender(it, "navodki") for it in items if isinstance(it, dict)]
        return SearchResult(
            query_echo=filter_.model_dump(mode="json"),
            total_estimated=payload.get("total"),
            page_size=len(tenders),
            tenders=tenders,
            search_meta=SearchMeta(took_ms=0, source="navodki", cache_hit=False),
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
                f"{NAVODKI_BASE}/tenders/{reg_number}",
                provider_name=self.info.label,
                headers=headers,
                expected_404_to_not_found=True,
            )
        except NotFoundError:
            raise
        if not isinstance(payload, dict) or not payload:
            raise NotFoundError(
                f"navodki.ru: тендер {reg_number} не найден.",
                details={"reg_number": reg_number, "provider": "navodki"},
            )
        return _parse_tender(payload, "navodki", reg_number_override=reg_number)


def _build_search_params(filter_: TenderSearchFilter) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": filter_.limit}
    if filter_.query:
        params["q"] = filter_.query
    if filter_.okpd2_codes:
        params["okpd"] = ",".join(filter_.okpd2_codes)
    if filter_.regions:
        params["region"] = ",".join(filter_.regions)
    if filter_.customer_inn:
        params["customer"] = filter_.customer_inn
    if filter_.price_min_rub is not None:
        params["price_min"] = filter_.price_min_rub
    if filter_.price_max_rub is not None:
        params["price_max"] = filter_.price_max_rub
    if filter_.smp_only:
        params["smp"] = 1
    return params


def _parse_tender(item: dict[str, Any], provider: str, *, reg_number_override: str | None = None) -> Tender:
    reg_number = reg_number_override or str(
        item.get("reg") or item.get("reg_number") or item.get("id") or ""
    )
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
        platform=Platform.OTHER,
        status=TenderStatus.UNKNOWN,
        smp_only=bool(item.get("smp")),
        url_eis=str(
            item.get("url")
            or f"https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={reg_number}"
        ),
        url_xml=f"https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html?regNumber={reg_number}",
        fetched_at=datetime.now(UTC),
        source_provider=provider,
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


__all__ = ["NavodkiProvider"]
