"""atomno-mcp-zakupki — MCP-сервер для портала zakupki.gov.ru.

Open-клиент семьи `atomno-mcp-*`. Open-source MIT, Phase 0–2 по SPEC
(`_knowledge/specs/spec.md`). 5 публичных тулзов: search_tenders,
get_tender, get_customer_history, get_supplier_stats, lookup_okpd2.

Цепочка провайдеров по умолчанию (API-only): damia → gosplan → navodki.
HTML-fallback — только явный opt-in через MCP_ZAKUPKI_PROVIDERS.
Опционально EIS-official через extra `[eis-official]`.
"""

__version__ = "0.1.0"
