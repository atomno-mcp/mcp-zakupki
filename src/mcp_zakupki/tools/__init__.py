"""Реализации MCP-тулзов open-клиента (SPEC §5.1–5.5)."""

from .get_customer_history import get_customer_history
from .get_supplier_stats import get_supplier_stats
from .get_tender import get_tender
from .lookup_okpd2 import lookup_okpd2
from .search_tenders import search_tenders

__all__ = [
    "search_tenders",
    "get_tender",
    "get_customer_history",
    "get_supplier_stats",
    "lookup_okpd2",
]
