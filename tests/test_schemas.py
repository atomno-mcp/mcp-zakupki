"""Тесты Pydantic-моделей и enum'ов."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydValidationError

from mcp_zakupki.schemas import (
    Customer,
    Document,
    FileType,
    LawType,
    LookupResult,
    OkpdEntry,
    Platform,
    SearchMeta,
    SearchResult,
    Tender,
    TenderSearchFilter,
    TenderStatus,
)


def _make_tender(**overrides) -> Tender:
    base = dict(
        reg_number="0173100007426000018",
        law_type=LawType.FZ_44,
        title="Разработка веб-портала",
        customer=Customer(inn="7707083893", short_name="ООО ПРИМЕР"),
        url_eis="https://example.ru/notice",
        fetched_at=datetime.now(UTC),
        source_provider="damia",
    )
    base.update(overrides)
    return Tender(**base)


class TestCustomer:
    def test_minimal_inn_and_name(self) -> None:
        c = Customer(inn="7707083893", short_name="X")
        assert c.inn == "7707083893"
        assert c.short_name == "X"
        assert c.region_code is None


class TestDocument:
    def test_default_file_type_other(self) -> None:
        d = Document(title="ТЗ", url="https://x.ru/file.pdf")
        assert d.file_type in {FileType.OTHER, "other"}


class TestTender:
    def test_minimal_construction(self) -> None:
        t = _make_tender()
        assert t.reg_number == "0173100007426000018"
        assert t.law_type in (LawType.FZ_44, "44-fz")

    def test_serialization_roundtrip(self) -> None:
        t = _make_tender(price_initial_rub=Decimal("1840000.00"))
        dumped = t.model_dump(mode="json")
        rebuilt = Tender.model_validate(dumped)
        assert rebuilt.reg_number == t.reg_number
        assert rebuilt.price_initial_rub == Decimal("1840000.00")


class TestSearchResult:
    def test_default_empty(self) -> None:
        sr = SearchResult()
        assert sr.tenders == []
        assert sr.search_meta.cache_hit is False

    def test_with_tenders(self) -> None:
        sr = SearchResult(
            tenders=[_make_tender()],
            page_size=1,
            search_meta=SearchMeta(took_ms=42, source="damia", cache_hit=False),
        )
        assert sr.page_size == 1
        assert sr.search_meta.source == "damia"


class TestTenderSearchFilter:
    def test_defaults_for_law_type(self) -> None:
        f = TenderSearchFilter()
        assert LawType.FZ_44 in f.law_type or "44-fz" in f.law_type

    def test_limit_default_20(self) -> None:
        f = TenderSearchFilter()
        assert f.limit == 20

    def test_limit_clamps_via_validator(self) -> None:
        with pytest.raises(PydValidationError):
            TenderSearchFilter(limit=0)
        with pytest.raises(PydValidationError):
            TenderSearchFilter(limit=101)


class TestLookupResult:
    def test_with_entries(self) -> None:
        r = LookupResult(
            query="разработка",
            results=[
                OkpdEntry(
                    code="62.01",
                    name="Услуги по разработке ПО",
                    type="okpd2",
                    level=2,
                    match_score=0.95,
                )
            ],
        )
        assert r.results[0].code == "62.01"
        assert 0.0 <= r.results[0].match_score <= 1.0


class TestEnumsBackwardsCompat:
    def test_platform_other_resolvable_from_str(self) -> None:
        assert Platform("other") == Platform.OTHER

    def test_status_unknown_default(self) -> None:
        t = _make_tender()
        assert t.status in (TenderStatus.UNKNOWN, "unknown")
