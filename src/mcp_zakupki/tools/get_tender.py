"""Tool `get_tender` — детали тендера по реестровому номеру (SPEC §5.2)."""

from __future__ import annotations

import time

from ..byok_limit import record_byok_usage
from ..cache import make_args_hash
from ..context import ServiceContext
from ..errors import McpZakupkiError
from ..schemas import Tender, TenderStatus
from ..tier import enforce_byok_if_needed
from ..validators import validate_reg_number

_TOOL_NAME = "get_tender"


async def get_tender(
    ctx: ServiceContext,
    *,
    reg_number: str,
    include_documents: bool = True,
    include_protocols: bool = False,
) -> Tender:
    started = time.perf_counter()
    norm_reg = validate_reg_number(reg_number)
    args_hash = make_args_hash(
        {
            "reg_number": norm_reg,
            "include_documents": include_documents,
            "include_protocols": include_protocols,
        }
    )

    cached = await ctx.cache.get_tender(norm_reg)
    if cached is not None:
        tender = Tender.model_validate(cached["tender"])
        await ctx.cache.write_audit(
            _TOOL_NAME,
            args_hash,
            provider=cached["provider"],
            cache_hit=True,
            status="ok",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return tender

    try:
        if ctx.config.hosted_mode_enabled and ctx.hosted is not None:
            tender = await ctx.hosted.get_tender(
                norm_reg,
                include_documents=include_documents,
                include_protocols=include_protocols,
            )
        else:
            await enforce_byok_if_needed(ctx)
            tender = await ctx.resolver.get_tender(
                norm_reg,
                include_documents=include_documents,
                include_protocols=include_protocols,
            )
            await record_byok_usage(ctx.cache, has_atomno_key=False)
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

    elapsed = int((time.perf_counter() - started) * 1000)
    is_completed = tender.status in {TenderStatus.COMPLETED, TenderStatus.CANCELLED}
    await ctx.cache.put_tender(
        norm_reg,
        tender.model_dump(mode="json"),
        provider=tender.source_provider,
        is_completed=is_completed,
        law_type=str(tender.law_type),
        title=tender.title,
        customer_inn=tender.customer.inn,
        customer_name=tender.customer.short_name,
        price_rub=float(tender.price_initial_rub) if tender.price_initial_rub is not None else None,
        publish_date=tender.publish_date.isoformat() if tender.publish_date else None,
        apps_deadline=tender.applications_deadline.isoformat()
        if tender.applications_deadline
        else None,
        platform=str(tender.platform) if tender.platform else None,
        status=str(tender.status),
        smp_only=tender.smp_only,
    )
    await ctx.cache.write_audit(
        _TOOL_NAME,
        args_hash,
        provider=tender.source_provider,
        cache_hit=False,
        status="ok",
        latency_ms=elapsed,
    )
    return tender


__all__ = ["get_tender"]
