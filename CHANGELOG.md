# Changelog

Все значимые изменения этого пакета документируются здесь.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added
- Скаффолд open-клиента `atomno-mcp-zakupki` (Phase 0 + начало Phase 1 по SPEC).
- `pyproject.toml` со SemVer-зависимостями и MAJOR-lock.
- CLI на `argparse` с флагами `--help`, `--version`, `--transport`,
  `--host`, `--port`, `--log-level`. Env `MCP_ZAKUPKI_LOG_LEVEL`.
- Pydantic-модели: `Tender`, `Customer`, `Document`, `Protocol`,
  `OkpdEntry`, `SearchResult`, `OrgHistorySummary`.
- Валидаторы: ИНН (10/12), ОГРН/ОГРНИП (13/15), реестровый номер (19–21),
  ОКПД2 (`^\d{2}(\.\d{1,2}){0,5}$`).
- SQLite-кэш через `aiosqlite` (search 1 ч / details 6 ч / org-history 24 ч /
  classifiers 30 дней) + audit-лог без PII.
- Провайдеры (заглушки + базовая HTTP-обвязка, retry/backoff через
  `tenacity`): DaMIA, ГосПлан API v2, navodki.ru, HTML-fallback (по
  `viewXml.html?regNumber=...`). EIS-official — через extra
  `[eis-official]` (опц.).
- 5 open-tools (Phase 1 по SPEC §5.1–5.5): `search_tenders`, `get_tender`,
  `get_customer_history`, `get_supplier_stats`, `lookup_okpd2`.
- Vendored-стартер справочника ОКПД2 (50+ кодов; полная база — через
  будущий CI-job).
- Тесты: `test_cli.py` (6 групп — TestHelp, TestVersion, TestTransport*,
  TestLogLevel*, TestParserDefaults, TestInvalidEnvBailsOutCleanly),
  `test_validators`, `test_schemas`, `test_cache`, `test_lookup_okpd2`.
- Dockerfile (multi-stage, Python 3.12-slim) + `.dockerignore`.
- `glama.json` для submission в каталог Glama.ai.

## [0.1.0] — TBD

Первый публичный релиз. Подробности будут зафиксированы в
секции `[0.1.0]` после публикации в PyPI и GitHub-release.

[Unreleased]: https://github.com/atomno-labs/mcp-zakupki/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/atomno-labs/mcp-zakupki/releases/tag/v0.1.0
