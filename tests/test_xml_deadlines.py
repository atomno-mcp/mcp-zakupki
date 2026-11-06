"""Regression tests for XML deadline parsing in html_fallback.

Tests use real EIS XML fixtures (no network access).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from mcp_zakupki.providers.html_fallback import _xml_to_tender

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "eis"


def _load_fixture(reg_number: str) -> ET.Element:
    path = FIXTURES_DIR / f"{reg_number}.xml"
    assert path.exists(), f"Fixture not found: {path}"
    tree = ET.parse(path)
    return tree.getroot()


def _make_tender(reg_number: str) -> object:
    root = _load_fixture(reg_number)
    return _xml_to_tender(
        root,
        reg_number,
        url_xml=f"https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html?regNumber={reg_number}",
        url_eis=f"https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={reg_number}",
        include_documents=False,
    )


class TestXmlDeadlineParsing:
    """Regression tests for contract_execution_end_date parsing."""

    def test_0139300020926000165_applications_deadline(self) -> None:
        tender = _make_tender("0139300020926000165")
        assert tender.applications_deadline is not None
        assert tender.applications_deadline.date() == date(2026, 11, 23)
        assert tender.applications_deadline_raw is not None
        assert "2026-11-23" in tender.applications_deadline_raw

    def test_0139300020926000165_applications_timezone(self) -> None:
        tender = _make_tender("0139300020926000165")
        assert tender.applications_timezone == "+07:00"

    def test_0139300020926000165_contract_execution_end_date(self) -> None:
        tender = _make_tender("0139300020926000165")
        # contractExecutionPaymentPlan/notRelativeTermsInfo/endDate =
        # 2028-02-22+03:00 — календарная дата, смещение +03:00 не время.
        assert tender.contract_execution_end_date == date(2028, 2, 22)
        assert tender.contract_execution_end_date_raw is not None
        assert "2028-02-22" in tender.contract_execution_end_date_raw
        assert "contract_execution_end_date" in tender.verified_fields
        assert not any(
            "contract_execution_end_date" in w for w in tender.parse_warnings
        )
        dumped = tender.model_dump(mode="json")
        assert dumped["contract_execution_end_date"] == "2028-02-22"
        assert "T03:00:00" not in str(dumped)

    def test_0121600019726000108_applications_deadline(self) -> None:
        tender = _make_tender("0121600019726000108")
        assert tender.applications_deadline is not None
        assert tender.applications_deadline.date() == date(2026, 11, 11)

    def test_0121600019726000108_applications_timezone(self) -> None:
        tender = _make_tender("0121600019726000108")
        assert tender.applications_timezone == "+03:00"

    def test_0121600019726000108_contract_execution_end_date(self) -> None:
        tender = _make_tender("0121600019726000108")
        # contractExecutionPaymentPlan/notRelativeTermsInfo/endDate =
        # 2026-12-31+03:00 — календарная дата.
        assert tender.contract_execution_end_date == date(2026, 12, 31)
        assert tender.contract_execution_end_date_raw is not None
        assert "2026-12-31" in tender.contract_execution_end_date_raw
        assert "contract_execution_end_date" in tender.verified_fields
        dumped = tender.model_dump(mode="json")
        assert dumped["contract_execution_end_date"] == "2026-12-31"

    def test_0203100000126000048_applications_deadline(self) -> None:
        tender = _make_tender("0203100000126000048")
        assert tender.applications_deadline is not None
        assert tender.applications_deadline.date() == date(2026, 11, 10)

    def test_0203100000126000048_applications_timezone(self) -> None:
        tender = _make_tender("0203100000126000048")
        assert tender.applications_timezone == "+03:00"

    def test_0203100000126000048_no_contract_exec_deadline(self) -> None:
        tender = _make_tender("0203100000126000048")
        # endDate в разделе исполнения контракта отсутствует → null + warning,
        # поле не попадает в verified_fields.
        assert tender.contract_execution_end_date is None
        assert tender.contract_execution_end_date_raw is None
        assert any("contract_execution_end_date" in w for w in tender.parse_warnings)
        assert "contract_execution_end_date" not in tender.verified_fields

    def test_smp_only_none_when_not_found(self) -> None:
        tender = _make_tender("0139300020926000165")
        assert tender.smp_only is None

    def test_parse_warnings_not_empty(self) -> None:
        tender = _make_tender("0139300020926000165")
        assert isinstance(tender.parse_warnings, list)
        assert isinstance(tender.verified_fields, list)
        assert len(tender.verified_fields) > 0

    def test_applications_deadline_datetime_with_tz(self) -> None:
        tender = _make_tender("0139300020926000165")
        assert tender.applications_deadline is not None
        assert tender.applications_deadline.tzinfo is not None

    def test_applications_timezone_populated(self) -> None:
        tender = _make_tender("0139300020926000165")
        assert tender.applications_timezone is not None
        assert tender.applications_timezone in {"+07:00", "+03:00"}

    def test_source_timezone_field_renamed(self) -> None:
        tender = _make_tender("0139300020926000165")
        assert not hasattr(tender, "source_timezone")
