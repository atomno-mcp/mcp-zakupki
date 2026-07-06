# Changelog

Все значимые изменения этого пакета документируются здесь.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [SemVer](https://semver.org/lang/ru/).

## [0.1.3] — 2026-07-06

### Changed

- Брендинг PyPI и публичной документации: `atomno-labs` → `Atomno` / `atomno-mcp`
  (автор, copyright, контактные URL).

## [0.1.2] — 2026-07-06

### Changed

- Нейтральная публичная документация и комментарии в коде (compliance / fair-use framing).

## [0.1.1] — 2026-07-06

### Fixed

- Убран неофициальный HTML-парсинг ЕИС: ненадёжный путь, не соответствующий ToS реестра
  (см. README). Опциональный fallback — только при явной настройке провайдеров и env.
- `get_customer_history` / `get_supplier_stats` работают через корпоративный API endpoint
  (`MCP_ZAKUPKI_ATOMNO_API_KEY`); для агрегированной аналитики нужен договорённый провайдер данных.
- Корпоративный endpoint `api.atomno-mcp.ru/zakupki/v1` для `search_tenders` / `get_tender`
  (бета; при отсутствии backend — понятное сообщение вместо тихого падения).
- Fair-use: дневной лимит **10 сетевых запросов** в демо-режиме без корпоративного ключа
  (`MCP_ZAKUPKI_BYOK_DAILY_LIMIT`).

### Changed

- Документация: рекомендуемый production-путь — корпоративный API-ключ Atomno или ключи
  лицензированных провайдеров (Damia, ГосПлан, navodki).

## [0.1.0] — 2026-07-06

Первый публичный релиз.

[0.1.3]: https://github.com/atomno-mcp/mcp-zakupki/releases/tag/v0.1.3
[0.1.2]: https://github.com/atomno-mcp/mcp-zakupki/releases/tag/v0.1.2
[0.1.1]: https://github.com/atomno-mcp/mcp-zakupki/releases/tag/v0.1.1
[0.1.0]: https://github.com/atomno-mcp/mcp-zakupki/releases/tag/v0.1.0
