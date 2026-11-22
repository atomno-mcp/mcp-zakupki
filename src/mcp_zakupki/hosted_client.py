"""HTTP-клиент API Закупок (api.atomno-mcp.ru)."""

from __future__ import annotations

from typing import Any, TypeVar

import httpx
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from . import __version__
from .config import AppConfig
from .errors import HostedApiUnavailableError, NotFoundError, ParseError, ValidationError
from .schemas import SearchResult, Tender, TenderSearchFilter
from .search_contract import hosted_search_payload

_UNAVAILABLE = "API Закупок не вернул результат."
_Model = TypeVar("_Model", bound=BaseModel)


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
            hosted_search_payload(filter_),
            SearchResult,
            required_field="tenders",
        )

    async def get_tender(
        self,
        reg_number: str,
        *,
        include_documents: bool = True,
        include_protocols: bool = False,
    ) -> Tender:
        if include_protocols:
            raise ValidationError(
                "API Закупок пока не загружает протоколы.",
                details={"field": "include_protocols"},
            )
        payload = {
            "reg_number": reg_number,
            "include_documents": include_documents,
            "include_protocols": include_protocols,
        }
        tender = await self._post_model("/tender", payload, Tender)
        if tender.reg_number != reg_number:
            raise ParseError(
                "API Закупок вернул карточку с другим реестровым номером.",
                details={"operation": "/tender", "reg_number": reg_number},
            )
        return tender

    async def get_customer_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_json("/customer-history", payload)

    async def get_supplier_stats(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_json("/supplier-stats", payload)

    async def _post_model(
        self,
        path: str,
        payload: dict[str, Any],
        model_cls: type[_Model],
        *,
        required_field: str | None = None,
    ) -> _Model:
        data = await self._post_json(path, payload)
        try:
            if required_field and required_field not in data:
                raise ParseError(
                    "В ответе API Закупок отсутствует обязательное поле.",
                    details={"operation": path, "field": required_field},
                )
            return model_cls.model_validate(data)
        except PydanticValidationError as exc:
            raise ParseError(
                "API Закупок вернул ответ в неожиданном формате.",
                details={"operation": path},
            ) from exc

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise HostedApiUnavailableError(
                "API Закупок не ответил за отведённое время.",
                details={"operation": path, "reason": "timeout"},
            ) from exc
        except httpx.HTTPError as exc:
            raise HostedApiUnavailableError(
                _UNAVAILABLE,
                details={"operation": path, "reason": type(exc).__name__},
            ) from exc

        if resp.is_error:
            detail, code = _extract_error(resp)
            details = {"operation": path, "http_status": resp.status_code}
            if resp.status_code == 422 or code == ValidationError.code:
                raise ValidationError(detail or "API Закупок отклонил параметры запроса.", details=details)
            if resp.status_code == 404 and code == NotFoundError.code:
                raise NotFoundError(detail or "Закупка с таким номером отсутствует в ответе ЕИС.", details=details)
            raise HostedApiUnavailableError(detail or _UNAVAILABLE, details=details)
        try:
            body = resp.json()
        except ValueError as exc:
            raise ParseError(
                "API Закупок вернул ответ в неожиданном формате.",
                details={"operation": path, "reason": "invalid_json"},
            ) from exc
        if not isinstance(body, dict):
            raise ParseError(
                "API Закупок вернул ответ в неожиданном формате.",
                details={"operation": path},
            )
        if body.get("error") == "not_implemented":
            raise HostedApiUnavailableError(
                body.get("message_ru") or _UNAVAILABLE,
                details=body.get("details") or {},
            )
        if body.get("status") in {
            "closed_beta",
            "coming_soon",
            "not_available",
        }:
            raise HostedApiUnavailableError(
                body.get("message_ru") or _UNAVAILABLE,
                details=body,
            )
        return body


def _extract_error(resp: httpx.Response) -> tuple[str | None, str | None]:
    try:
        body = resp.json()
    except ValueError:
        return None, None
    if isinstance(body, dict):
        for key in ("message_ru", "detail", "message", "error"):
            if isinstance(body.get(key), str) and body[key]:
                return body[key], body.get("error")
    return None, None
