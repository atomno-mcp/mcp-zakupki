"""atomno-mcp-zakupki — MCP-сервер для портала zakupki.gov.ru.

Open-клиент семьи `atomno-mcp-*`. Open-source MIT, Phase 0–2 по SPEC
(`_knowledge/specs/spec.md`). 5 публичных тулзов: search_tenders,
get_tender, get_customer_history, get_supplier_stats, lookup_okpd2.

Цепочка провайдеров по умолчанию: официальный сайт zakupki.gov.ru
(`eis_public`). Покупные посредники — только если явно заданы.
"""

__version__ = "0.2.0"
