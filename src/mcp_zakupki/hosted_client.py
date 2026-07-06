"""Тонкий HTTP-клиент к hosted API zakupki (api.atomno-mcp.ru).

Паттерн как у mcp-sudact-client: без парсеров, только REST + X-API-Key.
Пока backend не задеплоен — возвращает понятную ошибку «coming soon».
"""

from __future__ import annotations

from typing import Any

import httpx

from . import __version__
from .config import AppConfig
from .errors import HostedApiUnavailableError
from .schemas import SearchResult, Tender, TenderSearchFilter

_COMING_SOON = (
    "Hosted API для госзакупок ещё в разработке. "
    "Напишите hello@atomno.ru для раннего доступа или используйте BYOK "
    "(MCP_ZAKUPKI_DAMIA_KEY и др.) с дневным лимитом open-клиента."
)


class ZakupkiHostedClient:
    """REST-клиент к api.atomno-mcp.ru/zakupki/v1."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        api_key = config.atomno_api_key
        headers: dict[str, str] = {
            "User-Agent": config.user_agent.replace("0.1.0", __version__),
            "Accept": "application/json",
        }
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=config.providers.pro_base.rstrip("/"),
            timeout=config.http_timeout_s,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search_tenders(self, filter_: TenderSearchFilter) -> SearchResult:
        return await self._post_model(
            "/search",
            filter_.model_dump(mode="json", exclude_none=True),
            SearchResult,
            "search_tenders",
        )

    async def get_tender(
        self,
        reg_number: str,
        *,
        include_documents: bool = True,
        include_protocols: bool = False,
    ) -> Tender:
        payload = {
            "reg_number": reg_number,
            "include_documents": include_documents,
            "include_protocols": include_protocols,
        }
        return await self._post_model("/tender", payload, Tender, "get_tender")

    async def get_customer_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_json("/customer-history", payload)

    async def get_supplier_stats(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_json("/supplier-stats", payload)

    async def _post_model(
        self,
        path: str,
        payload: dict[str, Any],
        model_cls: type,
        op: str,
    ):
        data = await self._post_json(path, payload)
        return model_cls.model_validate(data)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise HostedApiUnavailableError(
                _COMING_SOON,
                details={"operation": path, "reason": "timeout"},
            ) from exc
        except httpx.HTTPError as exc:
            raise HostedApiUnavailableError(
                _COMING_SOON,
                details={"operation": path, "reason": type(exc).__name__},
            ) from exc

        if resp.status_code in (404, 501, 502, 503):
            raise HostedApiUnavailableError(
                _COMING_SOON,
                details={"operation": path, "http_status": resp.status_code},
            )
        if resp.status_code >= 400:
            detail = _extract_detail(resp)
            raise HostedApiUnavailableError(
                detail or _COMING_SOON,
                details={"operation": path, "http_status": resp.status_code},
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise HostedApiUnavailableError(
                _COMING_SOON,
                details={"operation": path, "reason": "invalid_json"},
            ) from exc
        if isinstance(body, dict) and body.get("error") == "not_implemented":
            raise HostedApiUnavailableError(
                body.get("message_ru") or _COMING_SOON,
                details=body.get("details") or {},
            )
        if isinstance(body, dict) and body.get("status") in {
            "closed_beta",
            "coming_soon",
            "not_available",
        }:
            raise HostedApiUnavailableError(
                body.get("message_ru") or _COMING_SOON,
                details=body,
            )
        return body


def _extract_detail(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300] or None
    if isinstance(body, dict):
        for key in ("message_ru", "detail", "message", "error"):
            if body.get(key):
                return str(body[key])
    return None
