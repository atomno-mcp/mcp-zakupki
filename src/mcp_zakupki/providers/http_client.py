"""Общая обвязка httpx.AsyncClient + retry для провайдеров.

Все провайдеры используют один и тот же helper для исходящих
запросов: единый User-Agent, таймауты, exponential backoff на 5xx /
429 / network errors через `tenacity`. Также маппит коды ответа в
типизированные исключения проекта (`AuthFailedError`, `RateLimitedError`,
`ProviderUnavailableError`, `NotFoundError`, `ParseError`).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import AppConfig
from ..errors import (
    AuthFailedError,
    NotFoundError,
    ParseError,
    ProviderUnavailableError,
    RateLimitedError,
)

logger = logging.getLogger(__name__)


class TransientUpstreamError(Exception):
    """Маркер «можно повторить» для tenacity (5xx, 429, сетевые ошибки)."""


class HttpAdapter:
    """Тонкий обёрточник над `httpx.AsyncClient` с фабрикой повторов."""

    def __init__(self, config: AppConfig, *, base_url: str | None = None) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._base_url = base_url

    async def __aenter__(self) -> HttpAdapter:
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "timeout": self._config.http_timeout_s,
                "headers": {"User-Agent": self._config.user_agent},
                "follow_redirects": True,
            }
            if self._base_url is not None:
                kwargs["base_url"] = self._base_url
            if self._config.http_proxy:
                kwargs["proxy"] = self._config.http_proxy
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        url: str,
        *,
        provider_name: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        data: Any = None,
        expected_404_to_not_found: bool = False,
    ) -> httpx.Response:
        try:
            return await self._request_with_retry(
                method,
                url,
                provider_name=provider_name,
                params=params,
                headers=headers,
                json_body=json_body,
                data=data,
                expected_404_to_not_found=expected_404_to_not_found,
            )
        except TransientUpstreamError as exc:
            raise ProviderUnavailableError(
                f"{provider_name}: исчерпан retry для {method} {url} ({exc}).",
                details={"provider": provider_name, "method": method, "url": url},
            ) from exc

    @retry(
        retry=retry_if_exception_type(TransientUpstreamError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.6, min=0.5, max=4.0),
        reraise=True,
    )
    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        provider_name: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        data: Any = None,
        expected_404_to_not_found: bool = False,
    ) -> httpx.Response:
        client = await self._ensure_client()
        try:
            response = await client.request(
                method,
                url,
                params=params,
                headers=headers,
                json=json_body,
                data=data,
            )
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            logger.warning("%s: network error %s — retry possible", provider_name, exc)
            raise TransientUpstreamError(str(exc)) from exc

        if response.status_code in {502, 503, 504}:
            raise TransientUpstreamError(
                f"{provider_name}: {response.status_code} for {method} {url}"
            )
        if response.status_code == 429:
            raise RateLimitedError(
                f"{provider_name} вернул 429 (rate limited) на {method} {url}.",
                details={"provider": provider_name, "status": 429},
            )
        if response.status_code in {401, 403}:
            raise AuthFailedError(
                f"{provider_name} вернул {response.status_code} (auth failed). "
                "Проверьте API-ключ / токен.",
                details={"provider": provider_name, "status": response.status_code},
            )
        if response.status_code == 404 and expected_404_to_not_found:
            raise NotFoundError(
                f"{provider_name}: ресурс не найден ({method} {url}).",
                details={"provider": provider_name, "status": 404},
            )
        if 500 <= response.status_code < 600:
            raise ProviderUnavailableError(
                f"{provider_name}: HTTP {response.status_code} на {method} {url}.",
                details={"provider": provider_name, "status": response.status_code},
            )
        if response.status_code >= 400:
            raise ParseError(
                f"{provider_name}: неожиданный HTTP {response.status_code} на {method} {url}.",
                details={"provider": provider_name, "status": response.status_code},
            )
        return response

    async def get_json(
        self,
        url: str,
        *,
        provider_name: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_404_to_not_found: bool = False,
    ) -> Any:
        resp = await self.request(
            "GET",
            url,
            provider_name=provider_name,
            params=params,
            headers=headers,
            expected_404_to_not_found=expected_404_to_not_found,
        )
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError(
                f"{provider_name}: ответ не JSON для GET {url}.",
                details={"provider": provider_name, "url": url},
            ) from exc

    async def get_text(
        self,
        url: str,
        *,
        provider_name: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_404_to_not_found: bool = False,
    ) -> str:
        resp = await self.request(
            "GET",
            url,
            provider_name=provider_name,
            params=params,
            headers=headers,
            expected_404_to_not_found=expected_404_to_not_found,
        )
        return resp.text
