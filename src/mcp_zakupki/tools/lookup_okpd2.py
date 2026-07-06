"""Tool `lookup_okpd2` — поиск кода ОКПД2 / КТРУ по тексту (SPEC §5.5).

Используется vendored seed (`data/okpd2_seed.json`) — стартер на 60+
кодов. После CI-загрузки полного справочника (Phase 1.x) seed
перезаписывается полным набором.

Алгоритм матчинга:
    1. Если seed ещё не залит в SQLite — лениво заливаем при первом вызове.
    2. Запрос нормализуется (lower, trim).
    3. Перебираем все классификаторы, считаем `match_score`:
       - +0.5 если все слова запроса встречаются в `name`.
       - +0.3 если запрос — точное префиксное совпадение с `code`.
       - +0.2 за процент общих слов / длину запроса.
    4. Сортируем убыванием score, режем top-N.

Это упрощённый алгоритм, достаточный для seed'а из 60 кодов. Полный
FTS5 + Levenshtein-rerank — Phase 1.x с полным справочником.
"""

from __future__ import annotations

import importlib.resources as importlib_resources
import json
import logging
import time
from typing import Any

from ..cache import CacheStore
from ..context import ServiceContext
from ..errors import ValidationError
from ..schemas import LookupResult, OkpdEntry

logger = logging.getLogger(__name__)

_TOOL_NAME = "lookup_okpd2"
_SEED_VERSION_KEY = "classifiers_version"


async def lookup_okpd2(
    ctx: ServiceContext,
    *,
    query: str,
    limit: int = 10,
    code_type: str = "okpd2",
) -> LookupResult:
    started = time.perf_counter()
    if not query or not query.strip():
        raise ValidationError(
            "lookup_okpd2: запрос не может быть пустым (3+ символов).",
            details={"query": query},
        )
    if len(query.strip()) < 3:
        raise ValidationError(
            "lookup_okpd2: запрос должен быть не короче 3 символов.",
            details={"query": query},
        )
    if code_type not in {"okpd2", "ktru", "both"}:
        raise ValidationError(
            "lookup_okpd2: code_type должен быть 'okpd2', 'ktru' или 'both'.",
            details={"code_type": code_type},
        )
    limit = max(1, min(50, limit))

    await _ensure_seed_loaded(ctx.cache)

    classifiers = await ctx.cache.list_classifiers()
    norm_query = query.strip().lower()
    query_words = [w for w in norm_query.replace(",", " ").split() if w]

    items: list[OkpdEntry] = []
    for c in classifiers:
        if code_type != "both" and c["type"] != code_type:
            continue
        score = _score(c, norm_query, query_words)
        if score <= 0:
            continue
        items.append(
            OkpdEntry(
                code=c["code"],
                name=c["name"],
                type=c["type"],  # type: ignore[arg-type]
                parent_code=c["parent_code"],
                level=c["level"],
                match_score=round(min(1.0, score), 3),
            )
        )

    items.sort(key=lambda e: e.match_score, reverse=True)
    items = items[:limit]

    await ctx.cache.write_audit(
        _TOOL_NAME,
        norm_query,
        provider="local",
        cache_hit=True,
        status="ok",
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    return LookupResult(query=query, results=items)


def _score(c: dict[str, Any], norm_query: str, query_words: list[str]) -> float:
    code = c["code"].lower()
    name = c["fts_text"]
    score = 0.0
    if query_words and all(w in name for w in query_words):
        score += 0.5
    if code.startswith(norm_query):
        score += 0.3
    if name.startswith(norm_query):
        score += 0.2
    if query_words:
        common = sum(1 for w in query_words if w in name)
        score += 0.2 * (common / len(query_words))
    return score


async def _ensure_seed_loaded(cache: CacheStore) -> None:
    version = await cache.get_meta(_SEED_VERSION_KEY)
    if version:
        return
    try:
        with importlib_resources.as_file(
            importlib_resources.files("mcp_zakupki.data").joinpath("okpd2_seed.json")
        ) as path:
            seed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - bundled file should exist
        logger.exception("Не удалось прочитать okpd2_seed.json")
        return
    items = seed.get("items") or []
    for item in items:
        await cache.upsert_classifier(
            code=str(item["code"]),
            type_=str(item.get("type", "okpd2")),
            parent_code=item.get("parent"),
            level=int(item.get("level", 1)),
            name=str(item.get("name", "")),
        )
    await cache.set_meta(_SEED_VERSION_KEY, str(seed.get("version", "okpd2-seed-v1")))
    logger.info("classifiers seeded: %d items (version=%s)", len(items), seed.get("version"))


__all__ = ["lookup_okpd2"]
