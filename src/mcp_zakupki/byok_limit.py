"""Дневной лимит BYOK-запросов без Atomno API-ключа (open-core moat v0.1.1)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from .cache import CacheStore
from .errors import RateLimitedError

DEFAULT_BYOK_DAILY_LIMIT = 10


async def enforce_byok_daily_limit(
    cache: CacheStore,
    *,
    limit: int = DEFAULT_BYOK_DAILY_LIMIT,
    has_atomno_key: bool,
) -> None:
    """Блокирует BYOK-вызовы после `limit` сетевых запросов в сутки.

    С Atomno API-ключом лимит не применяется (hosted billing на сервере).
    """
    if has_atomno_key or limit <= 0:
        return
    today = date.today().isoformat()
    count = await cache.get_byok_daily_count(today)
    if count >= limit:
        raise RateLimitedError(
            (
                f"Дневной лимит open-клиента без Atomno API-ключа исчерпан "
                f"({limit} запросов/сутки). "
                "Получите ключ на https://atomno-mcp.ru/pricing или завтра."
            ),
            details={
                "limit": limit,
                "used": count,
                "date": today,
                "hint": "MCP_ZAKUPKI_ATOMNO_API_KEY снимает лимит (hosted API).",
            },
        )
    await cache.increment_byok_daily_count(today)


async def record_byok_usage(cache: CacheStore, *, has_atomno_key: bool) -> None:
    if has_atomno_key:
        return
    await cache.increment_byok_daily_count(date.today().isoformat())


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
