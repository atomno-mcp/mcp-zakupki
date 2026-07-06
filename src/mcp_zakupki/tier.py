"""Проверки open-core tier (hosted vs BYOK vs Pro-only tools)."""

from __future__ import annotations

from .byok_limit import DEFAULT_BYOK_DAILY_LIMIT, enforce_byok_daily_limit
from .context import ServiceContext
from .errors import ProRequiredError

_ANALYTICS_HINT = (
    "Аналитика заказчиков/поставщиков доступна только через hosted API Atomno. "
    "Получите MCP_ZAKUPKI_ATOMNO_API_KEY: https://atomno-mcp.ru/pricing — "
    "или напишите hello@atomno.ru."
)


def require_atomno_key_for_analytics(ctx: ServiceContext) -> None:
    if not ctx.config.hosted_mode_enabled:
        raise ProRequiredError(
            _ANALYTICS_HINT,
            details={"tools": ["get_customer_history", "get_supplier_stats"]},
        )


async def enforce_byok_if_needed(ctx: ServiceContext) -> None:
    """Дневной лимит BYOK без Atomno-ключа (search/get_tender)."""
    if ctx.config.hosted_mode_enabled:
        return
    await enforce_byok_daily_limit(
        ctx.cache,
        limit=ctx.config.byok_daily_limit or DEFAULT_BYOK_DAILY_LIMIT,
        has_atomno_key=ctx.config.hosted_mode_enabled,
    )
