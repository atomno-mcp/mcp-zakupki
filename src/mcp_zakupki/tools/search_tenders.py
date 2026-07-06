"""Tool `search_tenders` — поиск тендеров по фильтрам (SPEC §5.1).

Логика (соответствует SPEC §5.1 «Логика реализации»):

    1. Валидация фильтров: regex для ИНН/ОГРН/ОКПД2, диапазонов цен, дат.
    2. Нормализация в `TenderSearchFilter`.
    3. Поиск в SQLite-кэше по `args_hash` (TTL 1 ч).
    4. Cache miss → каскадный fallback по цепочке провайдеров.
    5. Сохранение в кэш + audit-лог.
"""

from __future__ import annotations

import time

from ..byok_limit import record_byok_usage
from ..cache import make_args_hash
from ..context import ServiceContext
from ..errors import McpZakupkiError
from ..schemas import (
    LawType,
    Platform,
    SearchMeta,
    SearchResult,
    TenderSearchFilter,
    TenderStatus,
)
from ..validators import (
    validate_date_range,
    validate_inn,
    validate_ogrn,
    validate_okpd2_codes,
    validate_price_range,
    validate_region_code,
)
from ..tier import enforce_byok_if_needed

_TOOL_NAME = "search_tenders"


async def search_tenders(
    ctx: ServiceContext,
    *,
    law_type: list[str] | None = None,
    query: str | None = None,
    okpd2_codes: list[str] | None = None,
    ktru_codes: list[str] | None = None,
    regions: list[str] | None = None,
    customer_inn: str | None = None,
    customer_ogrn: str | None = None,
    platform: list[str] | None = None,
    price_min_rub: float | None = None,
    price_max_rub: float | None = None,
    status: list[str] | None = None,
    smp_only: bool = False,
    publish_date_from: str | None = None,
    publish_date_to: str | None = None,
    applications_deadline_from: str | None = None,
    applications_deadline_to: str | None = None,
    limit: int = 20,
    next_page_token: str | None = None,
) -> SearchResult:
    started = time.perf_counter()

    customer_inn_n = validate_inn(customer_inn)
    customer_ogrn_n = validate_ogrn(customer_ogrn)
    okpd2_norm = validate_okpd2_codes(okpd2_codes)
    regions_norm = [validate_region_code(r) for r in regions or []]
    pmin, pmax = validate_price_range(price_min_rub, price_max_rub)
    pub_from, pub_to = validate_date_range(
        publish_date_from,
        publish_date_to,
        field_from="publish_date_from",
        field_to="publish_date_to",
    )
    apps_from, apps_to = validate_date_range(
        applications_deadline_from,
        applications_deadline_to,
        field_from="applications_deadline_from",
        field_to="applications_deadline_to",
    )

    filter_ = TenderSearchFilter(
        law_type=_coerce_law(law_type),
        query=query,
        okpd2_codes=okpd2_norm,
        ktru_codes=ktru_codes or [],
        regions=regions_norm,
        customer_inn=customer_inn_n,
        customer_ogrn=customer_ogrn_n,
        platform=_coerce_platforms(platform),
        price_min_rub=pmin,
        price_max_rub=pmax,
        status=_coerce_statuses(status),
        smp_only=smp_only,
        publish_date_from=pub_from.isoformat() if pub_from else None,
        publish_date_to=pub_to.isoformat() if pub_to else None,
        applications_deadline_from=apps_from.isoformat() if apps_from else None,
        applications_deadline_to=apps_to.isoformat() if apps_to else None,
        limit=max(1, min(100, limit)),
        next_page_token=next_page_token,
    )

    args_hash = make_args_hash(filter_.model_dump(mode="json"))

    cached = await ctx.cache.get_search(args_hash)
    if cached is not None:
        result = SearchResult.model_validate(cached["results"])
        result.search_meta = SearchMeta(
            took_ms=int((time.perf_counter() - started) * 1000),
            source=cached["provider"],
            cache_hit=True,
        )
        await ctx.cache.write_audit(
            _TOOL_NAME,
            args_hash,
            provider=cached["provider"],
            cache_hit=True,
            status="ok",
            latency_ms=result.search_meta.took_ms,
        )
        return result

    try:
        if ctx.config.hosted_mode_enabled and ctx.hosted is not None:
            result = await ctx.hosted.search_tenders(filter_)
        else:
            await enforce_byok_if_needed(ctx)
            result = await ctx.resolver.search_tenders(filter_)
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

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    result.search_meta = SearchMeta(
        took_ms=elapsed_ms,
        source=result.search_meta.source or "unknown",
        cache_hit=False,
    )

    await ctx.cache.put_search(
        args_hash,
        filter_.model_dump(mode="json"),
        result.model_dump(mode="json"),
        provider=result.search_meta.source,
        next_page_token=result.next_page_token,
        total_estimated=result.total_estimated,
    )
    await ctx.cache.write_audit(
        _TOOL_NAME,
        args_hash,
        provider=result.search_meta.source,
        cache_hit=False,
        status="ok",
        latency_ms=elapsed_ms,
    )
    return result


def _coerce_law(values: list[str] | None) -> list[LawType]:
    if not values:
        return [LawType.FZ_44, LawType.FZ_223]
    out: list[LawType] = []
    for v in values:
        try:
            out.append(LawType(v))
        except ValueError:
            continue
    return out or [LawType.FZ_44, LawType.FZ_223]


def _coerce_platforms(values: list[str] | None) -> list[Platform]:
    if not values:
        return []
    out: list[Platform] = []
    for v in values:
        try:
            out.append(Platform(v))
        except ValueError:
            continue
    return out


def _coerce_statuses(values: list[str] | None) -> list[TenderStatus]:
    if not values:
        return []
    out: list[TenderStatus] = []
    for v in values:
        if v == "all_active":
            out.extend(
                [
                    TenderStatus.PUBLISHED,
                    TenderStatus.APPLICATIONS_OPEN,
                    TenderStatus.APPLICATIONS_CLOSED,
                ]
            )
            continue
        try:
            out.append(TenderStatus(v))
        except ValueError:
            continue
    return out


__all__ = ["search_tenders"]
