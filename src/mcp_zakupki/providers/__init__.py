"""Реализации провайдеров данных ЕИС.

Каждый провайдер — подкласс `BaseProvider` (см. `base.py`). На уровне
кулака выставлены 5 провайдеров:

    damia          — DaMIA API-Закупки (commercial)
    gosplan        — ГосПлан API v2 (commercial / sandbox)
    navodki        — navodki.ru (REST поверх ЕИС)
    eis_official   — официальный SOAP-сервис ЕИС (extra `[eis-official]`)
    html_fallback  — публичные страницы zakupki.gov.ru (последний рубеж)

Цепочка fallback'а собирается в `ProviderResolver` (см. `resolver.py`).
"""

from .base import BaseProvider, ProviderCapability
from .damia import DamiaProvider
from .gosplan import GosplanProvider
from .html_fallback import HtmlFallbackProvider
from .navodki import NavodkiProvider
from .resolver import ProviderResolver

__all__ = [
    "BaseProvider",
    "ProviderCapability",
    "DamiaProvider",
    "GosplanProvider",
    "HtmlFallbackProvider",
    "NavodkiProvider",
    "ProviderResolver",
]
