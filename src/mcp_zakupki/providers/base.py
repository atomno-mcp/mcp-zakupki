"""Базовый интерфейс для провайдеров данных ЕИС.

Все провайдеры реализуют общий контракт:

    async def search_tenders(filter: TenderSearchFilter) -> SearchResult
    async def get_tender(reg_number: str, *, include_documents=True,
                         include_protocols=False) -> Tender
    async def get_customer_history(inn=None, ogrn=None, period_from=None,
                                   period_to=None) -> OrgHistorySummary
    async def get_supplier_stats(inn=None, ogrn=None, period_from=None,
                                 period_to=None) -> OrgHistorySummary

Если провайдер не поддерживает метод — кидает `NotImplementedError`
(будет преобразовано в `provider_unavailable` на уровне резолвера).

Каждый провайдер также декларирует свои capabilities (`SEARCH`,
`TENDER_DETAILS`, `CUSTOMER_HISTORY`, `SUPPLIER_STATS`) — резолвер
использует их для каскадного выбора.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Flag, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..schemas import OrgHistorySummary, SearchResult, Tender, TenderSearchFilter


class ProviderCapability(Flag):
    """Какие методы поддерживает конкретный провайдер."""

    NONE = 0
    SEARCH = auto()
    TENDER_DETAILS = auto()
    CUSTOMER_HISTORY = auto()
    SUPPLIER_STATS = auto()
    ALL = SEARCH | TENDER_DETAILS | CUSTOMER_HISTORY | SUPPLIER_STATS


@dataclass(frozen=True)
class ProviderInfo:
    name: str  # "damia", "gosplan", "navodki", "html_fallback", "eis_official"
    label: str  # человекочитаемое имя для логов
    capabilities: ProviderCapability
    requires_auth: bool


class BaseProvider(ABC):
    """Абстрактный базовый класс провайдера.

    Конкретные подклассы должны переопределить `info` и реализовать
    методы из своих capabilities. Не поддерживаемые методы оставляют
    как `NotImplementedError` (см. дефолт-реализации ниже).
    """

    @property
    @abstractmethod
    def info(self) -> ProviderInfo:
        """Метаданные провайдера: имя, capabilities, требует ли auth."""

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def is_configured(self) -> bool:
        """Готов ли провайдер к работе (есть ключ / токен / нет требования)."""
        return True

    async def search_tenders(self, filter_: TenderSearchFilter) -> SearchResult:
        raise NotImplementedError(
            f"Провайдер {self.name!r} не поддерживает search_tenders."
        )

    async def get_tender(
        self,
        reg_number: str,
        *,
        include_documents: bool = True,
        include_protocols: bool = False,
    ) -> Tender:
        raise NotImplementedError(
            f"Провайдер {self.name!r} не поддерживает get_tender."
        )

    async def get_customer_history(
        self,
        *,
        inn: str | None = None,
        ogrn: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> OrgHistorySummary:
        raise NotImplementedError(
            f"Провайдер {self.name!r} не поддерживает get_customer_history."
        )

    async def get_supplier_stats(
        self,
        *,
        inn: str | None = None,
        ogrn: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> OrgHistorySummary:
        raise NotImplementedError(
            f"Провайдер {self.name!r} не поддерживает get_supplier_stats."
        )

    async def aclose(self) -> None:
        """Освободить ресурсы (закрыть httpx.AsyncClient и т.п.)."""
        return None

    # async-context support -----

    async def __aenter__(self) -> BaseProvider:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()
