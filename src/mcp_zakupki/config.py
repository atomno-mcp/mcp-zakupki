"""Сборка конфигурации `atomno-mcp-zakupki` из переменных окружения.

Все env-переменные имеют префикс `MCP_ZAKUPKI_` (канон семьи —
см. PRODUCTS/ATOMNO/AGENTS.md §2 Naming convention).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CACHE_FILENAME = "mcp_zakupki_cache.sqlite"
# API-only default chain (legal-first: no HTML scraping unless explicitly opted in).
DEFAULT_PROVIDER_CHAIN = ("damia", "gosplan", "navodki")
DEFAULT_USER_AGENT = "atomno-mcp-zakupki/0.1.0 (+https://github.com/atomno-labs/mcp-zakupki)"
DEFAULT_RPS = 30
DEFAULT_HTTP_TIMEOUT = 30.0


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
    extra: dict[str, str] = field(default_factory=dict)

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

        rps_raw = e.get("MCP_ZAKUPKI_RPS")
        rps = int(rps_raw) if rps_raw and rps_raw.isdigit() else DEFAULT_RPS

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
                pro_api_key=_strip_or_none(e.get("MCP_ZAKUPKI_API_KEY")),
                pro_base=e.get("MCP_ZAKUPKI_PRO_BASE", "https://api.atomno-mcp.ru/zakupki/v1"),
            ),
            chain=chain,
            rps=rps,
            http_timeout_s=float(e.get("MCP_ZAKUPKI_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT)),
            user_agent=e.get("MCP_ZAKUPKI_USER_AGENT", DEFAULT_USER_AGENT),
            http_proxy=_strip_or_none(e.get("MCP_ZAKUPKI_HTTP_PROXY")),
            log_level=e.get("MCP_ZAKUPKI_LOG_LEVEL", "INFO"),
        )

    def provider_status(self) -> dict[str, bool]:
        """Which network providers have credentials configured."""
        p = self.providers
        return {
            "damia": bool(p.damia_key),
            "gosplan": bool(p.gosplan_key),
            "navodki": bool(p.navodki_key),
            "eis_official": bool(p.eis_token),
            "html_fallback": "html_fallback" in self.chain,
        }

    @property
    def any_network_provider_configured(self) -> bool:
        """True if at least one API/EIS provider can serve network tools."""
        status = self.provider_status()
        return any(
            status[k]
            for k in ("damia", "gosplan", "navodki", "eis_official")
        )


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    return s or None
