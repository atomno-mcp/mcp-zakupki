"""Tool `get_customer_history` — история закупок заказчика (Pro hosted).

Агрегация удалена из BYOK open-path в v0.1.1 (moat). Только hosted API.
"""

from __future__ import annotations

import time
from datetime import date

from ..cache import make_args_hash
from ..context import ServiceContext
from ..errors import McpZakupkiError, ValidationError
from ..schemas import OrgHistorySummary
from ..tier import require_atomno_key_for_analytics
from ..validators import validate_inn, validate_ogrn

_TOOL_NAME = "get_customer_history"
_DEFAULT_FROM = "2024-01-01"


async def get_customer_history(
    ctx: ServiceContext,
    *,
    inn: str | None = None,
    ogrn: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> OrgHistorySummary:
    started = time.perf_counter()
    inn_n = validate_inn(inn)
    ogrn_n = validate_ogrn(ogrn)
    if inn_n is None and ogrn_n is None:
        raise ValidationError(
            "get_customer_history: укажите ИНН или ОГРН заказчика.",
            details={"inn": inn, "ogrn": ogrn},
        )
    pf = period_from or _DEFAULT_FROM
    pt = period_to or date.today().isoformat()

    require_atomno_key_for_analytics(ctx)

    ident = inn_n or ogrn_n or ""
    args_hash = make_args_hash(
        {"inn": inn_n, "ogrn": ogrn_n, "period_from": pf, "period_to": pt, "role": "customer"}
    )

    cached = await ctx.cache.get_org_history(ident, "customer", pf, pt)
    if cached is not None:
        summary = OrgHistorySummary.model_validate(cached["summary"])
        await ctx.cache.write_audit(
            _TOOL_NAME,
            args_hash,
            provider=cached["provider"],
            cache_hit=True,
            status="ok",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return summary

    try:
        assert ctx.hosted is not None
        raw = await ctx.hosted.get_customer_history(
            {"inn": inn_n, "ogrn": ogrn_n, "period_from": pf, "period_to": pt}
        )
        summary = OrgHistorySummary.model_validate(raw)
    except McpZakupkiError as exc:
        await ctx.cache.write_audit(
            _TOOL_NAME,
            args_hash,
            provider=None,
            cache_hit=False,
            status="error",
            latency_ms=int((time.perf_counter() - started) * 1000),
            error_class=type(exc).__name__,
        )
        raise

    await ctx.cache.put_org_history(
        ident,
        "customer",
        pf,
        pt,
        summary.model_dump(mode="json"),
        provider=summary.source_provider,
    )
    await ctx.cache.write_audit(
        _TOOL_NAME,
        args_hash,
        provider=summary.source_provider,
        cache_hit=False,
        status="ok",
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    return summary


__all__ = ["get_customer_history"]
