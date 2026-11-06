"""Тесты SQLite-кэша + audit-лога."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mcp_zakupki.cache import (
    CacheStore,
    make_args_hash,
)
from mcp_zakupki.schemas import (
    Customer,
    LawType,
    Tender,
)


@pytest.mark.asyncio
class TestSearchCache:
    async def test_miss_returns_none(self, cache: CacheStore) -> None:
        assert await cache.get_search("nonexistent") is None

    async def test_put_then_get(self, cache: CacheStore) -> None:
        await cache.put_search(
            "abc",
            {"q": "test"},
            {"tenders": [], "page_size": 0},
            provider="damia",
        )
        got = await cache.get_search("abc")
        assert got is not None
        assert got["provider"] == "damia"

    async def test_expired_returns_none(self, cache: CacheStore) -> None:
        await cache.put_search(
            "expired",
            {"q": "test"},
            {"tenders": []},
            provider="damia",
            ttl=timedelta(seconds=-1),
        )
        assert await cache.get_search("expired") is None


@pytest.mark.asyncio
class TestTenderCache:
    async def test_miss(self, cache: CacheStore) -> None:
        assert await cache.get_tender("0173100007426000018") is None

    async def test_put_then_get(self, cache: CacheStore) -> None:
        await cache.put_tender(
            "0173100007426000018",
            {"reg_number": "0173100007426000018", "title": "X"},
            provider="damia",
            law_type="44-fz",
            title="X",
        )
        got = await cache.get_tender("0173100007426000018")
        assert got is not None
        assert got["provider"] == "damia"

    @pytest.mark.parametrize(
        "smp_only",
        [None, False, True],
    )
    async def test_smp_only_round_trip(
        self, cache: CacheStore, smp_only: bool | None
    ) -> None:
        """smp_only None / False / True сохраняется и читается без искажения.

        None не должен превращаться в False.
        """
        tender = Tender(
            reg_number="0173100007426000018",
            law_type=LawType.FZ_44,
            title="X",
            customer=Customer(inn="7707083893", short_name="X"),
            url_eis="https://example.ru/notice",
            fetched_at=datetime.now(UTC),
            source_provider="damia",
            smp_only=smp_only,
        )
        raw = tender.model_dump(mode="json")
        # none / false / true должны остаться такими в сериализованной модели
        assert raw["smp_only"] is smp_only

        await cache.put_tender(
            tender.reg_number,
            raw,
            provider="damia",
            smp_only=smp_only,
        )
        got = await cache.get_tender(tender.reg_number)
        assert got is not None
        restored = Tender.model_validate(got["tender"])
        assert restored.smp_only is smp_only
        if smp_only is None:
            assert restored.smp_only is not False


@pytest.mark.asyncio
class TestOrgHistoryCache:
    async def test_put_then_get(self, cache: CacheStore) -> None:
        await cache.put_org_history(
            "7707083893",
            "customer",
            "2024-01-01",
            "2026-04-26",
            {"tenders_total": 10},
            provider="damia",
        )
        got = await cache.get_org_history(
            "7707083893", "customer", "2024-01-01", "2026-04-26"
        )
        assert got is not None
        assert got["summary"]["tenders_total"] == 10

    async def test_supplier_role_isolated(self, cache: CacheStore) -> None:
        await cache.put_org_history(
            "7707083893",
            "customer",
            "2024-01-01",
            "2026-04-26",
            {"role": "customer"},
            provider="damia",
        )
        got = await cache.get_org_history(
            "7707083893", "supplier", "2024-01-01", "2026-04-26"
        )
        assert got is None


@pytest.mark.asyncio
class TestClassifiers:
    async def test_upsert_and_list(self, cache: CacheStore) -> None:
        await cache.upsert_classifier(
            code="62.01",
            type_="okpd2",
            parent_code="62",
            level=2,
            name="Услуги по разработке ПО",
        )
        items = await cache.list_classifiers()
        assert any(i["code"] == "62.01" for i in items)


@pytest.mark.asyncio
class TestAudit:
    async def test_writes_record(self, cache: CacheStore) -> None:
        await cache.write_audit(
            "search_tenders",
            "h1",
            provider="damia",
            cache_hit=False,
            status="ok",
            latency_ms=412,
        )
        # Sanity: запись прошла без исключения. Чтение audit мы не реализуем
        # как публичный API, проверяем через прямой запрос:
        rows = await cache._fetch_all("SELECT tool_name, status FROM audit_log")
        assert ("search_tenders", "ok") in rows


class TestArgsHash:
    def test_stable_for_same_payload(self) -> None:
        a = make_args_hash({"a": 1, "b": 2})
        b = make_args_hash({"b": 2, "a": 1})
        assert a == b

    def test_different_for_different_payload(self) -> None:
        assert make_args_hash({"a": 1}) != make_args_hash({"a": 2})

    def test_handles_unicode(self) -> None:
        h = make_args_hash({"q": "разработка"})
        assert isinstance(h, str)
        assert len(h) == 64
