"""Сборка конфигурации `atomno-mcp-zakupki` из переменных окружения.

Все env-переменные имеют префикс `MCP_ZAKUPKI_` (канон семьи —
см. PRODUCTS/ATOMNO/AGENTS.md §2 Naming convention).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

DEFAULT_CACHE_FILENAME = "mcp_zakupki_cache.sqlite"
# API-only default chain (legal-first: no HTML scraping unless explicitly opted in).
DEFAULT_PROVIDER_CHAIN = ("damia", "gosplan", "navodki")
DEFAULT_USER_AGENT = f"atomno-mcp-zakupki/{__version__} (+https://github.com/atomno-mcp/mcp-zakupki)"
DEFAULT_RPS = 30
DEFAULT_HTTP_TIMEOUT = 30.0
DEFAULT_BYOK_DAILY_LIMIT = 10

ENV_ATOMNO_API_KEY = "MCP_ZAKUPKI_ATOMNO_API_KEY"
ENV_ATOMNO_API_KEY_LEGACY = "MCP_ZAKUPKI_API_KEY"
# Deprecated v0.1.1 — html_fallback удалён из open-клиента; env игнорируется.
ENV_ALLOW_HTML = "MCP_ZAKUPKI_ALLOW_HTML_SCRAPING"


@dataclass(frozen=True)
class ProvidersConfig:
    """Креды для подключаемых провайдеров. Любой может быть None."""

    damia_key: str | None = None
    gosplan_key: str | None = None
    gosplan_base: str = "https://v2.gosplan.info"
    navodki_key: str | None = None
    eis_token: str | None = None
    eis_base: str = "https://int.zakupki.gov.ru/eis-integration/services"
    pro_api_key: str | None = None
    pro_base: str = "https://api.atomno-mcp.ru/zakupki/v1"


@dataclass(frozen=True)
class AppConfig:
    """Корневая конфигурация (агрегатор всех настроек)."""

    cache_db: Path
    providers: ProvidersConfig
    chain: tuple[str, ...] = DEFAULT_PROVIDER_CHAIN
    rps: int = DEFAULT_RPS
    http_timeout_s: float = DEFAULT_HTTP_TIMEOUT
    user_agent: str = DEFAULT_USER_AGENT
    http_proxy: str | None = None
    log_level: str = "INFO"
    allow_html_scraping: bool = False
    byok_daily_limit: int = DEFAULT_BYOK_DAILY_LIMIT
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def atomno_api_key(self) -> str | None:
        return self.providers.pro_api_key

    @property
    def hosted_mode_enabled(self) -> bool:
        return bool(self.atomno_api_key)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AppConfig:
        e = env if env is not None else os.environ

        cache_db_raw = e.get("MCP_ZAKUPKI_CACHE_DB")
        cache_db = Path(cache_db_raw) if cache_db_raw else Path.cwd() / DEFAULT_CACHE_FILENAME

        chain_raw = e.get("MCP_ZAKUPKI_PROVIDERS")
        chain = (
            tuple(p.strip() for p in chain_raw.split(",") if p.strip())
            if chain_raw
            else DEFAULT_PROVIDER_CHAIN
        )

        allow_html = False  # v0.1.1: html_fallback удалён; MCP_ZAKUPKI_ALLOW_HTML_SCRAPING игнорируется
        if _truthy(e.get(ENV_ALLOW_HTML)):
            import logging

            logging.getLogger(__name__).warning(
                "%s игнорируется: html_fallback удалён из open-клиента v0.1.1.",
                ENV_ALLOW_HTML,
            )
        chain = _normalize_chain(chain)

        rps_raw = e.get("MCP_ZAKUPKI_RPS")
        rps = int(rps_raw) if rps_raw and rps_raw.isdigit() else DEFAULT_RPS

        byok_limit_raw = e.get("MCP_ZAKUPKI_BYOK_DAILY_LIMIT")
        byok_limit = (
            int(byok_limit_raw)
            if byok_limit_raw and byok_limit_raw.isdigit()
            else DEFAULT_BYOK_DAILY_LIMIT
        )

        atomno_key = _strip_or_none(e.get(ENV_ATOMNO_API_KEY)) or _strip_or_none(
            e.get(ENV_ATOMNO_API_KEY_LEGACY)
        )

        return cls(
            cache_db=cache_db,
            providers=ProvidersConfig(
                damia_key=_strip_or_none(e.get("MCP_ZAKUPKI_DAMIA_KEY")),
                gosplan_key=_strip_or_none(e.get("MCP_ZAKUPKI_GOSPLAN_KEY")),
                gosplan_base=e.get("MCP_ZAKUPKI_GOSPLAN_BASE", "https://v2.gosplan.info"),
                navodki_key=_strip_or_none(e.get("MCP_ZAKUPKI_NAVODKI_KEY")),
                eis_token=_strip_or_none(e.get("MCP_ZAKUPKI_EIS_TOKEN")),
                eis_base=e.get(
                    "MCP_ZAKUPKI_EIS_BASE",
                    "https://int.zakupki.gov.ru/eis-integration/services",
                ),
                pro_api_key=atomno_key,
                pro_base=e.get("MCP_ZAKUPKI_PRO_BASE", "https://api.atomno-mcp.ru/zakupki/v1"),
            ),
            chain=chain,
            rps=rps,
            http_timeout_s=float(e.get("MCP_ZAKUPKI_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT)),
            user_agent=e.get("MCP_ZAKUPKI_USER_AGENT", DEFAULT_USER_AGENT),
            http_proxy=_strip_or_none(e.get("MCP_ZAKUPKI_HTTP_PROXY")),
            log_level=e.get("MCP_ZAKUPKI_LOG_LEVEL", "INFO"),
            allow_html_scraping=allow_html,
            byok_daily_limit=byok_limit,
        )

    def provider_status(self) -> dict[str, bool]:
        """Which network providers have credentials configured."""
        p = self.providers
        return {
            "damia": bool(p.damia_key),
            "gosplan": bool(p.gosplan_key),
            "navodki": bool(p.navodki_key),
            "eis_official": bool(p.eis_token),
            "atomno_hosted": self.hosted_mode_enabled,
        }

    @property
    def any_network_provider_configured(self) -> bool:
        """True if hosted or at least one BYOK API provider can serve network tools."""
        if self.hosted_mode_enabled:
            return True
        status = self.provider_status()
        return any(
            status[k]
            for k in ("damia", "gosplan", "navodki", "eis_official")
        )


def _normalize_chain(chain: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for name in chain:
        if name in seen:
            continue
        if name == "html_fallback":
            continue
        out.append(name)
        seen.add(name)
    return tuple(out)


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    return s or None
