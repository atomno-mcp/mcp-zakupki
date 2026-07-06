"""Типизированные исключения для atomno-mcp-zakupki.

Иерархия (см. SPEC §4.1 FR-008):

    McpZakupkiError                — корень
        ValidationError            — невалидный вход (regex для ИНН/ОГРН/ОКПД2/reg_number)
        InvalidFilterCombination   — невалидная комбинация фильтров (например, дата_до < дата_от)
        NotFoundError              — тендер / заказчик / поставщик не найден
        ProviderUnavailableError   — все провайдеры отдали 5xx или таймаут
        AuthFailedError            — токен / API-ключ невалидный
        RateLimitedError           — превышен RPS у провайдера
        ParseError                 — провайдер изменил структуру ответа
        ConfigurationError         — невалидная конфигурация процесса (env vars)

Каждое исключение несёт человекочитаемое `message_ru` для агента и
опциональный `details` со структурированным контекстом.
"""

from __future__ import annotations

from typing import Any


class McpZakupkiError(Exception):
    """Базовое исключение пакета. Имеет `code` и `message_ru` для агента."""

    code: str = "mcp_zakupki_error"

    def __init__(
        self,
        message_ru: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message_ru = message_ru
        self.details = details or {}
        super().__init__(message_ru)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message_ru": self.message_ru,
            "details": self.details,
        }


class ValidationError(McpZakupkiError):
    """Вход не прошёл валидацию (regex, диапазоны, длина строк)."""

    code = "invalid_input"


class InvalidFilterCombination(McpZakupkiError):
    """Внутренне противоречивые фильтры (например, дата_до < дата_от)."""

    code = "invalid_filter_combination"


class NotFoundError(McpZakupkiError):
    """Объект не найден (тендер с таким reg_number, заказчик по ИНН и т.п.)."""

    code = "not_found"


class ProviderUnavailableError(McpZakupkiError):
    """Все настроенные провайдеры отдали 5xx или таймаут."""

    code = "provider_unavailable"


class AuthFailedError(McpZakupkiError):
    """Невалидный токен / API-ключ для провайдера."""

    code = "auth_failed"


class RateLimitedError(McpZakupkiError):
    """Превышен RPS у провайдера (429 или собственный лимит)."""

    code = "rate_limited"


class ParseError(McpZakupkiError):
    """Не удалось распарсить ответ источника (изменилась схема)."""

    code = "parse_error"


class ConfigurationError(McpZakupkiError):
    """Невалидная конфигурация процесса (например, MCP_ZAKUPKI_LOG_LEVEL=TRACE)."""

    code = "configuration_error"
