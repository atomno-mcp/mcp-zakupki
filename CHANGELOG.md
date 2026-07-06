# Changelog

Все значимые изменения этого пакета документируются здесь.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [SemVer](https://semver.org/lang/ru/).

## [0.1.1] — 2026-07-06

### Fixed

- **Open-core moat**: `html_fallback` (парсинг zakupki.gov.ru) отключён по умолчанию.
  Включается только при `MCP_ZAKUPKI_ALLOW_HTML_SCRAPING=1` **и** явном
  `html_fallback` в `MCP_ZAKUPKI_PROVIDERS` (на свой риск / правовые ограничения).
- `get_customer_history` / `get_supplier_stats` — только hosted API
  (`MCP_ZAKUPKI_ATOMNO_API_KEY`); BYOK-провайдеры больше не обслуживают аналитику.
- При `MCP_ZAKUPKI_ATOMNO_API_KEY` — thin-client к `api.atomno-mcp.ru/zakupki/v1`
  для `search_tenders` / `get_tender` (hosted backend в разработке → понятная ошибка).
- BYOK без Atomno-ключа: дневной лимит **10 сетевых запросов** (`MCP_ZAKUPKI_BYOK_DAILY_LIMIT`).

### Changed

- Рекомендуемый путь: hosted API-ключ. BYOK Damia/Gosplan/navodki — deprecated side-path.

## [0.1.0] — 2026-07-06

Первый публичный релиз.

[0.1.1]: https://github.com/atomno-mcp/mcp-zakupki/releases/tag/v0.1.1
[0.1.0]: https://github.com/atomno-mcp/mcp-zakupki/releases/tag/v0.1.0
