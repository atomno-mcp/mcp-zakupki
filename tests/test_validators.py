"""Тесты валидаторов входов MCP-тулзов (SPEC §5.x)."""

from __future__ import annotations

import pytest

from mcp_zakupki.errors import ValidationError
from mcp_zakupki.validators import (
    InvalidFilterRangeError,
    validate_date_range,
    validate_inn,
    validate_ogrn,
    validate_okpd2_code,
    validate_okpd2_codes,
    validate_price_range,
    validate_reg_number,
    validate_region_code,
)


class TestValidateInn:
    def test_legal_entity_inn_passes(self) -> None:
        assert validate_inn("7707083893") == "7707083893"

    def test_individual_inn_passes(self) -> None:
        assert validate_inn("123456789012") == "123456789012"

    def test_strips_whitespace(self) -> None:
        assert validate_inn("  7707083893  ") == "7707083893"

    def test_empty_returns_none(self) -> None:
        assert validate_inn(None) is None
        assert validate_inn("") is None
        assert validate_inn("   ") is None

    @pytest.mark.parametrize("bad", ["123", "abcdefghij", "770708389", "77070838933"])
    def test_invalid_format_raises(self, bad: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validate_inn(bad)
        assert "ИНН" in str(exc_info.value)


class TestValidateOgrn:
    def test_legal_entity_passes(self) -> None:
        assert validate_ogrn("1027700132195") == "1027700132195"

    def test_individual_passes(self) -> None:
        assert validate_ogrn("304770000123456") == "304770000123456"

    @pytest.mark.parametrize("bad", ["123", "abcdefghijklm", "10277001321"])
    def test_invalid_raises(self, bad: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validate_ogrn(bad)
        assert "ОГРН" in str(exc_info.value)


class TestValidateRegNumber:
    @pytest.mark.parametrize(
        "n",
        ["0173100007426000018", "01731000074260000180", "017310000742600001801"],
    )
    def test_valid_lengths_pass(self, n: str) -> None:
        assert validate_reg_number(n) == n

    def test_empty_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validate_reg_number(None)
        assert "реестровый" in str(exc_info.value).lower() or "обязательно" in str(exc_info.value).lower()
        with pytest.raises(ValidationError):
            validate_reg_number("")

    @pytest.mark.parametrize("bad", ["123", "abc", "01731000074", "0173100007426000018X"])
    def test_invalid_raises(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            validate_reg_number(bad)


class TestValidateOkpd2:
    @pytest.mark.parametrize(
        "code",
        ["62", "62.0", "62.01", "62.01.11", "62.01.11.000", "26.6"],
    )
    def test_valid(self, code: str) -> None:
        assert validate_okpd2_code(code) == code

    @pytest.mark.parametrize("bad", ["", "abc", "62.", ".62", "62.01.11.000.0.0.0"])
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            validate_okpd2_code(bad)

    def test_list_passes_each(self) -> None:
        assert validate_okpd2_codes(["62.01", "26.6"]) == ["62.01", "26.6"]

    def test_empty_or_none_returns_empty(self) -> None:
        assert validate_okpd2_codes(None) == []
        assert validate_okpd2_codes([]) == []


class TestValidateRegionCode:
    def test_pads_to_two_digits(self) -> None:
        assert validate_region_code("5") == "05"

    def test_two_digit_passes(self) -> None:
        assert validate_region_code("74") == "74"

    @pytest.mark.parametrize("bad", ["", "abc", "777"])
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            validate_region_code(bad)


class TestValidatePriceRange:
    def test_both_none(self) -> None:
        assert validate_price_range(None, None) == (None, None)

    def test_min_only(self) -> None:
        assert validate_price_range(100, None) == (100.0, None)

    def test_max_only(self) -> None:
        assert validate_price_range(None, 5_000_000) == (None, 5_000_000.0)

    def test_min_le_max(self) -> None:
        assert validate_price_range(100, 200) == (100.0, 200.0)

    def test_negative_min_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_price_range(-1, 100)

    def test_negative_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_price_range(0, -1)

    def test_min_gt_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_price_range(500, 100)


class TestValidateDateRange:
    def test_both_none(self) -> None:
        assert validate_date_range(None, None) == (None, None)

    def test_only_from(self) -> None:
        df, dt = validate_date_range("2026-01-01", None)
        assert df is not None and dt is None

    def test_from_le_to(self) -> None:
        df, dt = validate_date_range("2026-01-01", "2026-12-31")
        assert df is not None and dt is not None and df <= dt

    def test_invalid_date_raises(self) -> None:
        with pytest.raises(ValidationError):
            validate_date_range("not-a-date", None)

    def test_from_gt_to_raises(self) -> None:
        with pytest.raises(InvalidFilterRangeError):
            validate_date_range("2026-12-31", "2026-01-01")
