"""ProviderResolver — каскадный fallback по цепочке провайдеров.

Из конфига `MCP_ZAKUPKI_PROVIDERS=damia,gosplan,navodki` строится список
инстансов в порядке приоритета (API-only; HTML-scraping удалён из open-клиента
в v0.1.1 — см. hosted Pro на api.atomno-mcp.ru). На каждом MCP-вызове
резолвер обходит цепочку:

    1. Если провайдер не сконфигурирован (нет ключа) — пропускаем.
    2. Если провайдер не поддерживает запрашиваемый capability — пропускаем.
    3. Иначе — пробуем; при `NotFoundError` пробрасываем (не fallback'имся).
    4. При `AuthFailedError` / `ProviderUnavailableError` / `RateLimitedError`
       / `ParseError` — логируем и переходим к следующему.
    5. Если цепочка иссякла — кидаем `ProviderUnavailableError` с указанием
       всех опробованных провайдеров и причин.

Это даёт нам ровно ту надёжность, что описана в SPEC §7.5.6 (hybrid:
каскадный fallback) на стороне open-клиента.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from ..config import AppConfig
from ..errors import (
    AuthFailedError,
    NotFoundError,
    ParseError,
    ProviderUnavailableError,
    RateLimitedError,
)
from .base import BaseProvider, ProviderCapability
from .damia import DamiaProvider
from .gosplan import GosplanProvider
from .navodki import NavodkiProvider

if TYPE_CHECKING:
    from ..schemas import OrgHistorySummary, SearchResult, Tender, TenderSearchFilter

logger = logging.getLogger(__name__)

T = TypeVar("T")


# html_fallback удалён из open-клиента v0.1.1 — только private -server/.
_REMOVED_PROVIDERS = frozenset({"html_fallback"})

_BUILTIN_BUILDERS: dict[str, Callable[[AppConfig], BaseProvider]] = {
    "damia": lambda cfg: DamiaProvider(cfg),
    "gosplan": lambda cfg: GosplanProvider(cfg),
    "navodki": lambda cfg: NavodkiProvider(cfg),
}


class ProviderResolver:
    """Управляет цепочкой провайдеров и закрытием их ресурсов."""

    def __init__(
        self,
        config: AppConfig,
        *,
        chain: list[BaseProvider] | None = None,
    ) -> None:
        self._config = config
        self._chain: list[BaseProvider] = chain if chain is not None else _build_chain(config)

    @classmethod
    def from_env(cls, config: AppConfig | None = None) -> ProviderResolver:
        cfg = config or AppConfig.from_env()
        return cls(cfg)

    @property
    def chain(self) -> list[BaseProvider]:
        return list(self._chain)

    async def aclose(self) -> None:
        for p in self._chain:
            try:
                await p.aclose()
            except Exception:  # pragma: no cover - best-effort
                logger.exception("Ошибка при закрытии провайдера %s", p.name)

    async def __aenter__(self) -> ProviderResolver:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ---- public API ----------------------------------------------------

    async def search_tenders(self, filter_: TenderSearchFilter) -> SearchResult:
        return await self._run(
            ProviderCapability.SEARCH,
            "search_tenders",
            lambda p: p.search_tenders(filter_),
        )

    async def get_tender(
        self,
        reg_number: str,
        *,
        include_documents: bool = True,
        include_protocols: bool = False,
    ) -> Tender:
        return await self._run(
            ProviderCapability.TENDER_DETAILS,
            "get_tender",
            lambda p: p.get_tender(
                reg_number,
                include_documents=include_documents,
                include_protocols=include_protocols,
            ),
        )

    async def get_customer_history(
        self,
        *,
        inn: str | None,
        ogrn: str | None,
        period_from: str | None,
        period_to: str | None,
    ) -> OrgHistorySummary:
        return await self._run(
            ProviderCapability.CUSTOMER_HISTORY,
            "get_customer_history",
            lambda p: p.get_customer_history(
                inn=inn, ogrn=ogrn, period_from=period_from, period_to=period_to
            ),
        )

    async def get_supplier_stats(
        self,
        *,
        inn: str | None,
        ogrn: str | None,
        period_from: str | None,
        period_to: str | None,
    ) -> OrgHistorySummary:
        return await self._run(
            ProviderCapability.SUPPLIER_STATS,
            "get_supplier_stats",
            lambda p: p.get_supplier_stats(
                inn=inn, ogrn=ogrn, period_from=period_from, period_to=period_to
            ),
        )

    # ---- internal ------------------------------------------------------

    async def _run(
        self,
        capability: ProviderCapability,
        op_name: str,
        action: Callable[[BaseProvider], Awaitable[T]],
    ) -> T:
        attempted: list[dict[str, str]] = []
        last_exc: Exception | None = None
        for provider in self._chain:
            if not (provider.info.capabilities & capability):
                continue
            if provider.info.requires_auth and not provider.is_configured:
                attempted.append(
                    {"provider": provider.name, "reason": "not_configured"}
                )
                continue
            try:
                return await action(provider)
            except NotFoundError:
                raise
            except (
                AuthFailedError,
                RateLimitedError,
                ParseError,
                ProviderUnavailableError,
                NotImplementedError,
            ) as exc:
                logger.warning(
                    "%s: провайдер %s не отдал результат — %s",
                    op_name,
                    provider.name,
                    type(exc).__name__,
                )
                attempted.append(
                    {
                        "provider": provider.name,
                        "reason": type(exc).__name__,
                    }
                )
                last_exc = exc
                continue
        raise ProviderUnavailableError(
            f"Все провайдеры в цепочке исчерпаны для операции {op_name!r}.",
            details={
                "operation": op_name,
                "attempted": attempted,
                "last_error": type(last_exc).__name__ if last_exc else None,
            },
        )


# ---- builders -------------------------------------------------------------


def _build_chain(config: AppConfig) -> list[BaseProvider]:
    chain: list[BaseProvider] = []
    seen: set[str] = set()
    for name in config.chain:
        if name in seen:
            continue
        if name in _REMOVED_PROVIDERS:
            logger.warning(
                "Провайдер %r удалён из open-клиента v0.1.1 (HTML-scraping — hosted Pro).",
                name,
            )
            continue
        builder = _BUILTIN_BUILDERS.get(name)
        if builder is None:
            logger.warning("Неизвестный провайдер %r в MCP_ZAKUPKI_PROVIDERS", name)
            continue
        chain.append(builder(config))
        seen.add(name)
    if not chain:
        logger.warning(
            "Цепочка провайдеров пуста — сетевые тулзы недоступны. "
            "Работает только lookup_okpd2 (локальный справочник). "
            "Задайте MCP_ZAKUPKI_PROVIDERS и ключи провайдера."
        )
    return chain


__all__ = ["ProviderResolver"]
