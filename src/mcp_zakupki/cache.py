"""SQLite-кэш + audit-лог open-клиента (SPEC §8.1, §4.1 FR-009 / FR-016).

TTL по умолчанию:
    search           — 1 час
    tender details   — 6 часов (для активных), 30 дней (для completed)
    org-history      — 24 часа
    classifiers      — 30 дней

Все таблицы создаются лениво при первом обращении. Поверх — простая
тонкая обёртка над `aiosqlite`, без ORM.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# ---- TTL defaults ----------------------------------------------------------

TTL_SEARCH = timedelta(hours=1)
TTL_TENDER_ACTIVE = timedelta(hours=6)
TTL_TENDER_COMPLETED = timedelta(days=30)
TTL_ORG_HISTORY = timedelta(hours=24)
TTL_CLASSIFIERS = timedelta(days=30)

SCHEMA_VERSION = "1"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cache_search (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    args_hash       TEXT NOT NULL UNIQUE,
    filters_json    TEXT NOT NULL,
    results_json    TEXT NOT NULL,
    next_page_token TEXT,
    total_estimated INTEGER,
    provider        TEXT NOT NULL,
    fetched_at      DATETIME NOT NULL,
    cache_until     DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_search_until ON cache_search(cache_until);

CREATE TABLE IF NOT EXISTS cache_tenders (
    reg_number      TEXT PRIMARY KEY,
    law_type        TEXT NOT NULL,
    title           TEXT NOT NULL,
    customer_inn    TEXT,
    customer_name   TEXT,
    price_rub       REAL,
    publish_date    DATETIME,
    apps_deadline   DATETIME,
    platform        TEXT,
    status          TEXT,
    smp_only        INTEGER,
    raw_json        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    fetched_at      DATETIME NOT NULL,
    cache_until     DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_tenders_until ON cache_tenders(cache_until);
CREATE INDEX IF NOT EXISTS idx_cache_tenders_customer ON cache_tenders(customer_inn);

CREATE TABLE IF NOT EXISTS cache_org_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_inn         TEXT NOT NULL,
    org_role        TEXT NOT NULL,
    period_from     DATE NOT NULL,
    period_to       DATE NOT NULL,
    summary_json    TEXT NOT NULL,
    provider        TEXT NOT NULL,
    fetched_at      DATETIME NOT NULL,
    cache_until     DATETIME NOT NULL,
    UNIQUE(org_inn, org_role, period_from, period_to)
);
CREATE INDEX IF NOT EXISTS idx_cache_org_history_until ON cache_org_history(cache_until);

CREATE TABLE IF NOT EXISTS classifiers (
    code        TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    parent_code TEXT,
    level       INTEGER NOT NULL,
    name        TEXT NOT NULL,
    fts_text    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              DATETIME NOT NULL DEFAULT (datetime('now')),
    tool_name       TEXT NOT NULL,
    args_hash       TEXT NOT NULL,
    provider        TEXT,
    cache_hit       INTEGER NOT NULL,
    status          TEXT NOT NULL,
    latency_ms      INTEGER,
    error_class     TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS cache_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def make_args_hash(payload: Any) -> str:
    """Стабильный sha256-хэш от любой JSON-сериализуемой структуры."""
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class CacheStore:
    """Тонкая обёртка над `aiosqlite` с TTL-таблицами.

    Жизненный цикл:
        store = CacheStore(path)
        await store.init()         # создаёт схему если нет
        ...
        await store.close()

    Или async-context:
        async with CacheStore(path) as store:
            ...
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> CacheStore:
        await self.init()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def init(self) -> None:
        if self._db is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.execute(
            "INSERT OR IGNORE INTO cache_meta (key, value) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is None:
            return
        await self._db.close()
        self._db = None

    async def get_meta(self, key: str) -> str | None:
        row = await self._fetch_one(
            "SELECT value FROM cache_meta WHERE key = ?",
            (key,),
        )
        return row[0] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO cache_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self._db.commit()

    # ---- search cache --------------------------------------------------

    async def get_search(self, args_hash: str) -> dict[str, Any] | None:
        row = await self._fetch_one(
            "SELECT results_json, provider, fetched_at, cache_until "
            "FROM cache_search WHERE args_hash = ? AND cache_until > ?",
            (args_hash, _iso(utc_now())),
        )
        if not row:
            return None
        results_json, provider, fetched_at, _cache_until = row
        return {
            "results": json.loads(results_json),
            "provider": provider,
            "fetched_at": fetched_at,
        }

    async def put_search(
        self,
        args_hash: str,
        filters: dict[str, Any],
        results: dict[str, Any],
        provider: str,
        ttl: timedelta = TTL_SEARCH,
        next_page_token: str | None = None,
        total_estimated: int | None = None,
    ) -> None:
        assert self._db is not None
        now = utc_now()
        until = now + ttl
        await self._db.execute(
            "INSERT INTO cache_search "
            "(args_hash, filters_json, results_json, next_page_token, "
            "total_estimated, provider, fetched_at, cache_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(args_hash) DO UPDATE SET "
            "filters_json=excluded.filters_json, "
            "results_json=excluded.results_json, "
            "next_page_token=excluded.next_page_token, "
            "total_estimated=excluded.total_estimated, "
            "provider=excluded.provider, "
            "fetched_at=excluded.fetched_at, "
            "cache_until=excluded.cache_until",
            (
                args_hash,
                json.dumps(filters, sort_keys=True, ensure_ascii=False, default=str),
                json.dumps(results, sort_keys=True, ensure_ascii=False, default=str),
                next_page_token,
                total_estimated,
                provider,
                _iso(now),
                _iso(until),
            ),
        )
        await self._db.commit()

    # ---- tender cache --------------------------------------------------

    async def get_tender(self, reg_number: str) -> dict[str, Any] | None:
        row = await self._fetch_one(
            "SELECT raw_json, provider, fetched_at, cache_until "
            "FROM cache_tenders WHERE reg_number = ? AND cache_until > ?",
            (reg_number, _iso(utc_now())),
        )
        if not row:
            return None
        raw_json, provider, fetched_at, _cache_until = row
        return {
            "tender": json.loads(raw_json),
            "provider": provider,
            "fetched_at": fetched_at,
        }

    async def put_tender(
        self,
        reg_number: str,
        tender_json: dict[str, Any],
        provider: str,
        *,
        is_completed: bool = False,
        law_type: str | None = None,
        title: str | None = None,
        customer_inn: str | None = None,
        customer_name: str | None = None,
        price_rub: float | None = None,
        publish_date: str | None = None,
        apps_deadline: str | None = None,
        platform: str | None = None,
        status: str | None = None,
        smp_only: bool = False,
    ) -> None:
        assert self._db is not None
        now = utc_now()
        ttl = TTL_TENDER_COMPLETED if is_completed else TTL_TENDER_ACTIVE
        until = now + ttl
        await self._db.execute(
            "INSERT INTO cache_tenders "
            "(reg_number, law_type, title, customer_inn, customer_name, "
            "price_rub, publish_date, apps_deadline, platform, status, "
            "smp_only, raw_json, provider, fetched_at, cache_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(reg_number) DO UPDATE SET "
            "law_type=excluded.law_type, title=excluded.title, "
            "customer_inn=excluded.customer_inn, customer_name=excluded.customer_name, "
            "price_rub=excluded.price_rub, publish_date=excluded.publish_date, "
            "apps_deadline=excluded.apps_deadline, platform=excluded.platform, "
            "status=excluded.status, smp_only=excluded.smp_only, "
            "raw_json=excluded.raw_json, provider=excluded.provider, "
            "fetched_at=excluded.fetched_at, cache_until=excluded.cache_until",
            (
                reg_number,
                law_type or "",
                title or "",
                customer_inn,
                customer_name,
                price_rub,
                publish_date,
                apps_deadline,
                platform,
                status,
                int(smp_only),
                json.dumps(tender_json, sort_keys=True, ensure_ascii=False, default=str),
                provider,
                _iso(now),
                _iso(until),
            ),
        )
        await self._db.commit()

    # ---- org history cache --------------------------------------------

    async def get_org_history(
        self,
        org_inn: str,
        org_role: str,
        period_from: str,
        period_to: str,
    ) -> dict[str, Any] | None:
        row = await self._fetch_one(
            "SELECT summary_json, provider, fetched_at FROM cache_org_history "
            "WHERE org_inn = ? AND org_role = ? AND period_from = ? AND period_to = ? "
            "AND cache_until > ?",
            (org_inn, org_role, period_from, period_to, _iso(utc_now())),
        )
        if not row:
            return None
        summary_json, provider, fetched_at = row
        return {
            "summary": json.loads(summary_json),
            "provider": provider,
            "fetched_at": fetched_at,
        }

    async def put_org_history(
        self,
        org_inn: str,
        org_role: str,
        period_from: str,
        period_to: str,
        summary: dict[str, Any],
        provider: str,
        ttl: timedelta = TTL_ORG_HISTORY,
    ) -> None:
        assert self._db is not None
        now = utc_now()
        until = now + ttl
        await self._db.execute(
            "INSERT INTO cache_org_history "
            "(org_inn, org_role, period_from, period_to, summary_json, "
            "provider, fetched_at, cache_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(org_inn, org_role, period_from, period_to) DO UPDATE SET "
            "summary_json=excluded.summary_json, provider=excluded.provider, "
            "fetched_at=excluded.fetched_at, cache_until=excluded.cache_until",
            (
                org_inn,
                org_role,
                period_from,
                period_to,
                json.dumps(summary, sort_keys=True, ensure_ascii=False, default=str),
                provider,
                _iso(now),
                _iso(until),
            ),
        )
        await self._db.commit()

    # ---- classifiers ---------------------------------------------------

    async def list_classifiers(self) -> list[dict[str, Any]]:
        rows = await self._fetch_all(
            "SELECT code, type, parent_code, level, name, fts_text FROM classifiers"
        )
        return [
            {
                "code": r[0],
                "type": r[1],
                "parent_code": r[2],
                "level": r[3],
                "name": r[4],
                "fts_text": r[5],
            }
            for r in rows
        ]

    async def upsert_classifier(
        self,
        code: str,
        type_: str,
        parent_code: str | None,
        level: int,
        name: str,
    ) -> None:
        assert self._db is not None
        fts_text = f"{code} {name}".lower()
        await self._db.execute(
            "INSERT INTO classifiers (code, type, parent_code, level, name, fts_text) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET "
            "type=excluded.type, parent_code=excluded.parent_code, "
            "level=excluded.level, name=excluded.name, fts_text=excluded.fts_text",
            (code, type_, parent_code, level, name, fts_text),
        )
        await self._db.commit()

    # ---- audit ---------------------------------------------------------

    async def write_audit(
        self,
        tool_name: str,
        args_hash: str,
        *,
        provider: str | None,
        cache_hit: bool,
        status: str,
        latency_ms: int | None = None,
        error_class: str | None = None,
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO audit_log "
            "(tool_name, args_hash, provider, cache_hit, status, latency_ms, error_class) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                tool_name,
                args_hash,
                provider,
                int(cache_hit),
                status,
                latency_ms,
                error_class,
            ),
        )
        await self._db.commit()

    # ---- internals -----------------------------------------------------

    async def _fetch_one(self, sql: str, params: tuple) -> tuple | None:
        assert self._db is not None
        async with self._db.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _fetch_all(self, sql: str, params: tuple = ()) -> list[tuple]:
        assert self._db is not None
        async with self._db.execute(sql, params) as cur:
            return list(await cur.fetchall())
