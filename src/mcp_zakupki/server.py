"""FastMCP entrypoint для atomno-mcp-zakupki.

Регистрирует 5 open-tools (SPEC §5.1–5.5):

    * search_tenders          — поиск по 30+ фильтрам
    * get_tender              — карточка тендера по реестровому номеру
    * get_customer_history    — история закупок заказчика
    * get_supplier_stats      — статистика поставщика
    * lookup_okpd2            — поиск кода ОКПД2 / КТРУ по тексту

Сервис-контекст (`ServiceContext`) создаётся лениво при первом вызове,
общий на жизненный цикл процесса. Закрытие httpx-клиентов и SQLite —
через `atexit`-хук.

CLI argparse соответствует канону семьи `atomno-mcp-*` (см.
PRODUCTS/ATOMNO/_knowledge/MCP_BUILD_CHECKLIST.md §2): флаги `--help`,
`--version`, `--transport`, `--host`, `--port`, `--log-level`.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import logging
import os
import sys
from typing import Any

from fastmcp import FastMCP

from . import __version__
from .config import DEFAULT_PROVIDER_CHAIN, AppConfig
from .context import ServiceContext
from .errors import McpZakupkiError
from .tools import (
    get_customer_history as _get_customer_history_impl,
)
from .tools import (
    get_supplier_stats as _get_supplier_stats_impl,
)
from .tools import (
    get_tender as _get_tender_impl,
)
from .tools import (
    lookup_okpd2 as _lookup_okpd2_impl,
)
from .tools import (
    search_tenders as _search_tenders_impl,
)

logger = logging.getLogger("mcp_zakupki")

mcp: FastMCP = FastMCP(
    name="atomno-mcp-zakupki",
    instructions=(
        "Сервер для работы с порталом российских госзакупок zakupki.gov.ru "
        "(44-ФЗ, 223-ФЗ, 615-ПП). Open-tools: search_tenders, get_tender, "
        "lookup_okpd2 (офлайн-справочник ОКПД2). Источники search/get — "
        "только легальные API (DaMIA / ГосПлан / navodki) с BYOK-ключом; "
        "HTML-scraping удалён из open-клиента (v0.1.1). "
        "get_customer_history и get_supplier_stats — Pro hosted API "
        "(MCP_ZAKUPKI_API_KEY, https://atomno-mcp.ru/pricing#zakupki-pro). "
        "Для production рекомендуется корпоративный API Atomno; self-hosted с ключом "
        "лицензированного провайдера — для разработки и пилотов. "
        "Pro-функции (AI-summary, win_probability, watch) — на api.atomno-mcp.ru."
    ),
)


_ctx: ServiceContext | None = None
_ctx_lock = asyncio.Lock()


async def _get_ctx() -> ServiceContext:
    global _ctx
    if _ctx is not None:
        return _ctx
    async with _ctx_lock:
        if _ctx is None:
            ctx = ServiceContext.from_env()
            await ctx.__aenter__()
            _ctx = ctx
            atexit.register(_close_ctx_atexit)
    assert _ctx is not None
    return _ctx


def _close_ctx_atexit() -> None:
    if _ctx is None:
        return
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_ctx.__aexit__(None, None, None))
        loop.close()
    except Exception:  # pragma: no cover - best-effort cleanup
        pass


def _err(exc: McpZakupkiError) -> dict[str, Any]:
    return exc.to_dict()


# ---- MCP tools ------------------------------------------------------------


@mcp.tool()
async def ping() -> dict[str, Any]:
    """Диагностический тул: убедиться, что сервер запущен и доступен."""
    cfg = AppConfig.from_env()
    return {
        "ok": True,
        "service": "atomno-mcp-zakupki",
        "version": __version__,
        "cache_db": str(cfg.cache_db),
        "providers": list(cfg.chain),
    }


@mcp.tool()
async def search_tenders(
    law_type: list[str] | None = None,
    query: str | None = None,
    okpd2_codes: list[str] | None = None,
    ktru_codes: list[str] | None = None,
    regions: list[str] | None = None,
    customer_inn: str | None = None,
    customer_ogrn: str | None = None,
    platform: list[str] | None = None,
    price_min_rub: float | None = None,
    price_max_rub: float | None = None,
    status: list[str] | None = None,
    smp_only: bool = False,
    publish_date_from: str | None = None,
    publish_date_to: str | None = None,
    applications_deadline_from: str | None = None,
    applications_deadline_to: str | None = None,
    limit: int = 20,
    next_page_token: str | None = None,
) -> dict[str, Any]:
    """Найти тендеры (44-ФЗ / 223-ФЗ / 615-ПП) по фильтрам в ЕИС zakupki.gov.ru.

    Возвращает структурированный массив до 100 объектов на вызов с пагинацией
    через `next_page_token`. Все фильтры комбинируются через AND.

    Args:
        law_type: список из ["44-fz", "223-fz", "615-pp"]; default — оба ФЗ.
        query: свободный поиск по наименованию объекта закупки.
        okpd2_codes: коды ОКПД2 (можно префиксы: "62.0" включит всё под 62.0).
        ktru_codes: коды КТРУ для более точной фильтрации.
        regions: коды субъектов РФ ("66" = Свердловская обл., "74" = Челябинская).
        customer_inn: ИНН заказчика (10 цифр — юр.лицо, 12 — ИП).
        customer_ogrn: ОГРН заказчика (13 — юр.лицо, 15 — ИП).
        platform: ЭТП ("sberbank_ast", "rts_tender", "roseltorg", "tek_torg",
            "gpb", "rad", "fabrikant", "agz_rt").
        price_min_rub / price_max_rub: диапазон НМЦК в рублях.
        status: статус тендера ("published", "applications_open",
            "applications_closed", "in_progress", "completed", "cancelled",
            "all_active").
        smp_only: только закупки среди субъектов малого предпринимательства.
        publish_date_from / publish_date_to: диапазон даты публикации (YYYY-MM-DD).
        applications_deadline_from / applications_deadline_to: диапазон срока подачи заявок.
        limit: количество объектов в ответе (1–100, default 20).
        next_page_token: токен следующей страницы из предыдущего ответа.
    """
    try:
        ctx = await _get_ctx()
        result = await _search_tenders_impl(
            ctx,
            law_type=law_type,
            query=query,
            okpd2_codes=okpd2_codes,
            ktru_codes=ktru_codes,
            regions=regions,
            customer_inn=customer_inn,
            customer_ogrn=customer_ogrn,
            platform=platform,
            price_min_rub=price_min_rub,
            price_max_rub=price_max_rub,
            status=status,
            smp_only=smp_only,
            publish_date_from=publish_date_from,
            publish_date_to=publish_date_to,
            applications_deadline_from=applications_deadline_from,
            applications_deadline_to=applications_deadline_to,
            limit=limit,
            next_page_token=next_page_token,
        )
        return result.model_dump(mode="json")
    except McpZakupkiError as exc:
        return _err(exc)


@mcp.tool()
async def get_tender(
    reg_number: str,
    include_documents: bool = True,
    include_protocols: bool = False,
) -> dict[str, Any]:
    """Получить полную карточку тендера по реестровому номеру ЕИС.

    Args:
        reg_number: реестровый номер тендера (19–21 цифр).
            Пример: "0173100007426000018".
        include_documents: включить URLs всех приложенных документов.
        include_protocols: включить протоколы (более тяжёлый вызов).
    """
    try:
        ctx = await _get_ctx()
        tender = await _get_tender_impl(
            ctx,
            reg_number=reg_number,
            include_documents=include_documents,
            include_protocols=include_protocols,
        )
        return tender.model_dump(mode="json")
    except McpZakupkiError as exc:
        return _err(exc)


@mcp.tool()
async def get_customer_history(
    inn: str | None = None,
    ogrn: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> dict[str, Any]:
    """История закупок заказчика по ИНН или ОГРН (Pro hosted API).

    Требует `MCP_ZAKUPKI_API_KEY`. Агрегация истории заказчика — Pro-функция;
    open-клиент не выполняет её через BYOK-провайдеры.

    Args:
        inn: ИНН заказчика (10 цифр для юр.лица, 12 для ИП).
        ogrn: ОГРН/ОГРНИП заказчика.
        period_from: начало периода (YYYY-MM-DD, default 2024-01-01).
        period_to: конец периода (YYYY-MM-DD, default — сегодня).
    """
    try:
        ctx = await _get_ctx()
        summary = await _get_customer_history_impl(
            ctx,
            inn=inn,
            ogrn=ogrn,
            period_from=period_from,
            period_to=period_to,
        )
        return summary.model_dump(mode="json")
    except McpZakupkiError as exc:
        return _err(exc)


@mcp.tool()
async def get_supplier_stats(
    inn: str | None = None,
    ogrn: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> dict[str, Any]:
    """Статистика поставщика по ИНН или ОГРН (Pro hosted API).

    Требует `MCP_ZAKUPKI_API_KEY`. Агрегация статистики поставщика — Pro-функция.

    Args:
        inn: ИНН поставщика.
        ogrn: ОГРН/ОГРНИП поставщика.
        period_from / period_to: период анализа (YYYY-MM-DD).
    """
    try:
        ctx = await _get_ctx()
        summary = await _get_supplier_stats_impl(
            ctx,
            inn=inn,
            ogrn=ogrn,
            period_from=period_from,
            period_to=period_to,
        )
        return summary.model_dump(mode="json")
    except McpZakupkiError as exc:
        return _err(exc)


@mcp.tool()
async def lookup_okpd2(
    query: str,
    limit: int = 10,
    code_type: str = "okpd2",
) -> dict[str, Any]:
    """Найти код ОКПД2 / КТРУ по тексту запроса (полнотекстовый поиск).

    Использует vendored-справочник из 60+ топ-кодов (Phase 0–1) или
    полный справочник после CI-загрузки. Нет сетевых обращений.

    Args:
        query: текст запроса (минимум 3 символа). Пример: "разработка веб-портала".
        limit: количество результатов (1–50, default 10).
        code_type: "okpd2" (default), "ktru" или "both".
    """
    try:
        ctx = await _get_ctx()
        result = await _lookup_okpd2_impl(
            ctx,
            query=query,
            limit=limit,
            code_type=code_type,
        )
        return result.model_dump(mode="json")
    except McpZakupkiError as exc:
        return _err(exc)


# ---- CLI ------------------------------------------------------------------

_SUPPORTED_TRANSPORTS = ("stdio", "http", "sse", "streamable-http")
_DEFAULT_TRANSPORT = "stdio"
_DEFAULT_HTTP_HOST = "127.0.0.1"
_DEFAULT_HTTP_PORT = 8000
_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atomno-mcp-zakupki",
        description=(
            "MCP-сервер для российских госзакупок (zakupki.gov.ru, 44-ФЗ / "
            "223-ФЗ / 615-ПП). 5 open-tools: search_tenders, get_tender, "
            "get_customer_history, get_supplier_stats, lookup_okpd2. "
            "По умолчанию запускается по MCP stdio-транспорту для интеграции "
            "с Cursor, Claude Desktop, Cline, Goose и другими MCP-клиентами."
        ),
        epilog=(
            "Примеры:\n"
            "  atomno-mcp-zakupki                            # запуск для MCP-клиента через stdio\n"
            "  atomno-mcp-zakupki --transport http --port 8000\n"
            "  atomno-mcp-zakupki --log-level DEBUG\n"
            "  atomno-mcp-zakupki --check-config\n"
            "\n"
            "Переменные окружения:\n"
            "  MCP_ZAKUPKI_LOG_LEVEL    — DEBUG/INFO/WARNING/ERROR/CRITICAL (по умолчанию INFO).\n"
            "  MCP_ZAKUPKI_CACHE_DB     — путь к SQLite-файлу кэша.\n"
            "  MCP_ZAKUPKI_PROVIDERS    — цепочка провайдеров через запятую\n"
            f"                              (default: {','.join(DEFAULT_PROVIDER_CHAIN)}).\n"
            "  MCP_ZAKUPKI_DAMIA_KEY    — API-ключ DaMIA (api.damia.ru/zakupki).\n"
            "  MCP_ZAKUPKI_GOSPLAN_KEY  — API-ключ ГосПлан API v2.\n"
            "  MCP_ZAKUPKI_NAVODKI_KEY  — API-ключ navodki.ru.\n"
            "  MCP_ZAKUPKI_API_KEY      — Pro hosted API (обязателен для "
            "get_customer_history / get_supplier_stats).\n"
            "\n"
            "HTML-fallback удалён из open-клиента в v0.1.1 — используйте "
            "hosted Pro или BYOK API-провайдеры.\n"
            "\n"
            "Документация: https://github.com/atomno-mcp/mcp-zakupki"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"atomno-mcp-zakupki {__version__}",
        help="показать версию пакета и выйти",
    )
    parser.add_argument(
        "--transport",
        "-t",
        choices=_SUPPORTED_TRANSPORTS,
        default=_DEFAULT_TRANSPORT,
        help=(
            f"MCP-транспорт (по умолчанию: {_DEFAULT_TRANSPORT}). "
            "stdio — для локальных MCP-клиентов; http/sse/streamable-http — для сетевых."
        ),
    )
    parser.add_argument(
        "--host",
        default=_DEFAULT_HTTP_HOST,
        help=(
            f"Хост для http/sse/streamable-http транспортов (по умолчанию: {_DEFAULT_HTTP_HOST}). "
            "Игнорируется для stdio."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_HTTP_PORT,
        help=(
            f"Порт для http/sse/streamable-http транспортов (по умолчанию: {_DEFAULT_HTTP_PORT}). "
            "Игнорируется для stdio."
        ),
    )
    parser.add_argument(
        "--log-level",
        "-l",
        choices=_VALID_LOG_LEVELS,
        default=None,
        help=(
            "Уровень логирования; перекрывает переменную MCP_ZAKUPKI_LOG_LEVEL. "
            "По умолчанию INFO."
        ),
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help=(
            "Проверить конфигурацию (цепочка провайдеров, ключи, кэш) и выйти. "
            "Не запускает MCP-сервер."
        ),
    )
    return parser


def _resolve_log_level(cli_value: str | None) -> str:
    """CLI-флаг имеет приоритет над env; фолбэк — INFO.

    Никаких silent-fallback на невалидные значения — env с
    нераспознанным level взрывается ValueError, который main() превращает
    в exit-code 2 (SPEC §4.1 FR-008 + MCP_BUILD_CHECKLIST §2).
    """
    if cli_value is not None:
        return cli_value
    env_raw = os.environ.get("MCP_ZAKUPKI_LOG_LEVEL")
    if env_raw is None:
        return "INFO"
    env_norm = env_raw.strip().upper()
    if env_norm in _VALID_LOG_LEVELS:
        return env_norm
    raise ValueError(
        f"MCP_ZAKUPKI_LOG_LEVEL={env_raw!r} — недопустимое значение. "
        f"Допустимые: {', '.join(_VALID_LOG_LEVELS)}."
    )


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI.

    Args:
        argv: список аргументов (без имени программы). Если None —
            берётся из sys.argv[1:]. Возвращает exit-code:
            0 — штатное завершение, 2 — ошибка конфигурации.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        log_level = _resolve_log_level(args.log_level)
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover - parser.error вызывает SystemExit(2)

    cfg = AppConfig.from_env()

    if args.check_config:
        status = cfg.provider_status()
        lines = [
            f"atomno-mcp-zakupki {__version__}",
            f"  provider_chain:      {','.join(cfg.chain)}",
            f"  cache_db:            {cfg.cache_db}",
            f"  http_timeout:        {cfg.http_timeout_s}s",
            f"  rps:                 {cfg.rps}",
            f"  log_level:           {log_level}",
            "  providers:",
        ]
        for name, configured in status.items():
            lines.append(f"    {name:<16} {'configured' if configured else 'not configured'}")
        if not cfg.any_network_provider_configured:
            lines.append(
                "  WARN: ни один API-провайдер не настроен — "
                "сетевые тулзы вернут provider_unavailable. "
                "lookup_okpd2 работает офлайн. Добавьте MCP_ZAKUPKI_DAMIA_KEY "
                "или другой ключ (см. README)."
            )
        else:
            lines.append("OK: конфигурация валидна, есть хотя бы один API-провайдер.")
        sys.stdout.write("\n".join(lines) + "\n")
        return 0

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "atomno-mcp-zakupki %s starting (transport=%s, cache=%s, providers=%s)",
        __version__,
        args.transport,
        cfg.cache_db,
        ",".join(cfg.chain),
    )

    run_kwargs: dict[str, Any] = {"transport": args.transport}
    if args.transport in {"http", "sse", "streamable-http"}:
        run_kwargs["host"] = args.host
        run_kwargs["port"] = args.port
    mcp.run(**run_kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
