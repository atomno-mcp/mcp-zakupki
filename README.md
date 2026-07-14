<!-- mcp-name: io.github.atomno-mcp/mcp-zakupki -->
# atomno-mcp-zakupki

> **MCP server for Russian government procurement data** (44-FZ / 223-FZ /
> 615-PP) — find tenders, customer/supplier history, OKPD2 lookup directly
> from Cursor, Claude Desktop, Cline, Goose and any MCP-compatible client.

[![PyPI version](https://img.shields.io/pypi/v/atomno-mcp-zakupki.svg)](https://pypi.org/project/atomno-mcp-zakupki/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-2025--06--18-purple.svg)](https://modelcontextprotocol.io)

`atomno-mcp-zakupki` — это [MCP](https://modelcontextprotocol.io)-сервер
(Model Context Protocol — открытый протокол подключения LLM-клиентов к
внешним инструментам), который превращает портал ЕИС
[`zakupki.gov.ru`](https://zakupki.gov.ru) в набор тулзов для AI-ассистента.

Установили локально, прописали в конфиге Cursor / Claude Desktop / Cline —
и в одном промпте просите: *«Покажи свежие тендеры за сегодня по ОКПД2
62.0 в Москве с НМЦК ≤ 5 млн ₽»*. Сервер сходит в выбранный источник
данных, нормализует ответ и вернёт LLM готовую структуру.

## Зачем

| Боль | Сейчас | С `atomno-mcp-zakupki` |
|---|---|---|
| Каждое утро 30–60 мин просеивать ЕИС вручную или платить 16–67 K ₽/год за Контур / Seldon | Excel-сводки + email-уведомления | Один MCP-вызов из Cursor → LLM сама фильтрует, объясняет, сравнивает |
| Скрипты на Python с `ftp.zakupki.gov.ru` сломались с января 2025 | Ищут замену через коммерческие API | Hybrid-провайдеры: DaMIA, ГосПлан API v2, navodki; опц. ЕИС-токен |
| AI-агент не может прочитать тендер сам | Копи-паст в чат | `get_tender(reg_number)` отдаёт всю карточку как JSON |

## Что внутри (open-клиент)

| Tool | Tier | Что делает |
|---|---|---|
| `search_tenders` | **Self-hosted** | Поиск тендеров по 30+ фильтрам. Требует API-ключ DaMIA / ГосПлан / navodki. |
| `get_tender` | **Self-hosted** | Карточка тендера по реестровому номеру. Требует API-ключ провайдера. |
| `lookup_okpd2` | **Free** | Поиск кода ОКПД2 / КТРУ по тексту (локальный справочник, без сети). |
| `get_customer_history` | **Pro hosted** | История закупок заказчика — только `MCP_ZAKUPKI_API_KEY`. |
| `get_supplier_stats` | **Pro hosted** | Статистика поставщика — только `MCP_ZAKUPKI_API_KEY`. |

> **Production:** рекомендуется корпоративный API Atomno (`MCP_ZAKUPKI_API_KEY` на
> [`api.atomno-mcp.ru/zakupki/`](https://atomno-mcp.ru/pricing#zakupki-pro)).
> Self-hosted (свой ключ DaMIA/ГосПлан) — для разработки и пилотов; агрегированная
> аналитика и расширенные отчёты доступны через корпоративный endpoint.

> **v0.1.1:** убран неофициальный HTML-парсинг ЕИС. Инструменты `get_customer_history`
> и `get_supplier_stats` требуют корпоративного API-ключа.

## Quick start

### 1. Установка

```bash
# Через uvx (рекомендуется — без установки в систему):
uvx atomno-mcp-zakupki --help

# Через pipx:
pipx install atomno-mcp-zakupki
atomno-mcp-zakupki --version

# В виртуальное окружение проекта:
pip install atomno-mcp-zakupki
```

Опциональный экстра для официального ЕИС-токена (требует сертификат Минфина):

```bash
pip install "atomno-mcp-zakupki[eis-official]"
```

### 2. Конфигурация

**Для production** — получите hosted Pro-ключ:
[`atomno-mcp.ru/pricing#zakupki-pro`](https://atomno-mcp.ru/pricing#zakupki-pro)
→ `MCP_ZAKUPKI_API_KEY`.

**Для разработки (self-hosted)** — ключ одного API-провайдера
(DaMIA / ГосПлан / navodki) для `search_tenders` и `get_tender`.
Без ключей работает только `lookup_okpd2` (локальный справочник ОКПД2).

> **HTML-fallback удалён в v0.1.1.** Парсинг `zakupki.gov.ru` без API —
> только на hosted backend (private server, Phase 2).

| Переменная | Описание | Где взять |
|---|---|---|
| `MCP_ZAKUPKI_DAMIA_KEY` | API-ключ DaMIA API-Закупки | <https://damia.ru/apizakupki> (free-тариф «Старт» ≈ 100 запросов/мес) |
| `MCP_ZAKUPKI_GOSPLAN_KEY` | API-ключ ГосПлан API v2 | <https://wiki.gosplan.info> (sandbox без регистрации: `fz44test.gosplan.info`) |
| `MCP_ZAKUPKI_NAVODKI_KEY` | API-ключ navodki.ru | <https://navodki.ru> |
| `MCP_ZAKUPKI_EIS_TOKEN` | Токен ЕИС (через `pmd/auth/welcome`) | <https://zakupki.gov.ru/pmd/auth/welcome> + сертификат Минфина |
| `MCP_ZAKUPKI_API_KEY` | **Pro hosted** — обязателен для `get_customer_history` / `get_supplier_stats` и будущих AI-тулов | <https://atomno-mcp.ru/pricing#zakupki-pro> |
| `MCP_ZAKUPKI_LOG_LEVEL` | DEBUG / INFO / WARNING / ERROR / CRITICAL | по умолчанию `INFO` |
| `MCP_ZAKUPKI_CACHE_DB` | Путь к SQLite-кэшу | по умолчанию `./mcp_zakupki_cache.sqlite` |
| `MCP_ZAKUPKI_PROVIDERS` | Цепочка fallback'а (BYOK) | по умолчанию `damia,gosplan,navodki` |
| `MCP_ZAKUPKI_RPS` | Лимит запросов в минуту | по умолчанию `30` |

### 3. Подключение к Cursor

Откройте `~/.cursor/mcp.json` (или `Cursor → Settings → Model Context
Protocol`) и добавьте:

```json
{
  "mcpServers": {
    "zakupki": {
      "command": "uvx",
      "args": ["atomno-mcp-zakupki"],
      "env": {
        "MCP_ZAKUPKI_DAMIA_KEY": "your-damia-key-here",
        "MCP_ZAKUPKI_LOG_LEVEL": "INFO"
      }
    }
  }
}
```

### 4. Подключение к Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json` (Windows) или
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "zakupki": {
      "command": "uvx",
      "args": ["atomno-mcp-zakupki"]
    }
  }
}
```

### 5. Подключение к Cline / Goose / Kiro

Любой MCP-клиент: запустите `atomno-mcp-zakupki` (по умолчанию stdio
транспорт) и пропишите в его конфиге команду запуска.

Для HTTP-режима:

```bash
atomno-mcp-zakupki --transport http --host 127.0.0.1 --port 8765
```

## Примеры

### Утренний мониторинг тендеров

```
[Cursor]: Покажи свежие тендеры за сегодня по ОКПД2 62.0 в УрФО
          с НМЦК 500K-3M ₽, СМП-only.

→ search_tenders(
    okpd2_codes=["62.01", "62.02", "62.09"],
    regions=["66", "74", "59", "45", "72", "86", "89"],
    price_min_rub=500000, price_max_rub=3000000,
    smp_only=True,
    publish_date_from="2026-04-26",
    status=["applications_open"]
  )
← 7 тендеров найдено за 412 мс.
```

### Карточка тендера

```
[Cursor]: Расскажи подробно про реестровый номер 0173100007426000018.

→ get_tender(reg_number="0173100007426000018", include_documents=True)
← Все поля: заказчик, НМЦК, документация (PDF), сроки, площадка...
```

### Анализ заказчика (Pro)

```
[Cursor]: Кто такой ИНН 7449023800? Покажи историю закупок за 2 года.

→ get_customer_history(inn="7449023800", period_from="2024-01-01")
← Требуется MCP_ZAKUPKI_API_KEY (hosted Pro).
```

### Подбор кода ОКПД2

```
[Cursor]: Какой ОКПД2 у разработки веб-портала?

→ lookup_okpd2(query="разработка веб-портала", limit=5)
← [{"code":"62.01.11.000","name":"Услуги по проектированию ИС","match":0.94}, ...]
```

## CLI

```text
atomno-mcp-zakupki --help
atomno-mcp-zakupki --version
atomno-mcp-zakupki --check-config
atomno-mcp-zakupki --log-level DEBUG
atomno-mcp-zakupki --transport http --host 127.0.0.1 --port 8765
atomno-mcp-zakupki --transport sse --port 9000
```

Полный список опций — в `atomno-mcp-zakupki --help`.

## Источники данных

| Провайдер | Тариф | Что покрывает | Когда используется |
|---|---|---|---|
| **DaMIA API-Закупки** | Free «Старт» ≈ 100/мес → per-request | 44-ФЗ + 223-ФЗ + 615-ПП, методы `zakupka`, `contract`, `zsearch`, `customer`, `eruz`, `rnp` | Default 1-й (если есть `MCP_ZAKUPKI_DAMIA_KEY`) |
| **ГосПлан API v2** | Free sandbox + платные планы | 44-ФЗ (Beta), 615-ПП (Pre1), 223-ФЗ (Pre2) | Fallback 2-й |
| **navodki.ru** | Free | 44-ФЗ + 223-ФЗ | Fallback 3-й |
| **ЕИС-официальный** (опц.) | Free для физ.лица с токеном | SOAP `int.zakupki.gov.ru` | Если есть `MCP_ZAKUPKI_EIS_TOKEN` |

Цепочка BYOK: `MCP_ZAKUPKI_PROVIDERS=damia,gosplan,navodki`.
HTML-scraping **удалён** в v0.1.1 — используйте hosted Pro.
Если ни один не отвечает, тулза вернёт типизированную ошибку
`provider_unavailable`.

## Архитектура (вкратце)

```
AI-клиент (Cursor / Claude / Cline) ──MCP──▶ atomno-mcp-zakupki
                                              │
                                              ├──▶ lookup_okpd2 (offline)
                                              ├──▶ Self-hosted API (search/get)
                                              └──▶ hosted Pro (api.atomno-mcp.ru) — production
```

Все провайдеры реализуют общий `BaseProvider`-интерфейс. Ответы
нормализуются в Pydantic-модели `Tender`, `Customer`, `Document`,
`OrgHistorySummary`.

## Разработка

```bash
git clone https://github.com/atomno-mcp/mcp-zakupki.git
cd mcp-zakupki

python -m venv .venv
source .venv/bin/activate            # Linux/macOS
# .venv\Scripts\activate              # Windows

pip install -e ".[dev]"
pytest -v --cov=src/mcp_zakupki
ruff check src tests
```

## Disclaimer

- Сервис **не аффилирован** с Министерством финансов РФ, ФАС, оператором ЕИС
  или конкретными ЭТП.
- Данные ЕИС получаем через **лицензированные API-провайдеры**, официальный
  ЕИС-токен или **hosted backend** Atomno — не через scraping open-клиента.
- HTML-fallback убран в v0.1.1 (соответствие ToS реестра закупок).
- Используйте на свой риск. Решения о подаче заявок на тендеры — на
  ответственности пользователя.

## Лицензия

[MIT](LICENSE) © 2026 Atomno.

## Семья MCP-серверов atomno

`atomno-mcp-zakupki` — пятый сервер семьи `atomno-mcp-*`:

- [`atomno-mcp-cbr-rates`](https://github.com/atomno-mcp/mcp-cbr-rates) — курсы ЦБ РФ
- [`atomno-mcp-egrul`](https://github.com/atomno-mcp/mcp-egrul) — реквизиты юр.лиц из ЕГРЮЛ
- [`atomno-mcp-fns-check`](https://github.com/atomno-mcp/mcp-fns-check) — налоговая благонадёжность
- [`atomno-mcp-fssp`](https://github.com/atomno-mcp/mcp-fssp) — задолженности и исп. производства (готовится)
- **`atomno-mcp-zakupki`** — *вы здесь*
- [`atomno-mcp-rosreestr`](https://github.com/atomno-mcp/mcp-rosreestr) — недвижимость, кадастр (готовится)
- [`atomno-mcp-sudact`](https://github.com/atomno-mcp/mcp-sudact) — судебная практика (готовится)

Подключите все вместе — и получите полное B2G-досье по тендеру за один промпт.
