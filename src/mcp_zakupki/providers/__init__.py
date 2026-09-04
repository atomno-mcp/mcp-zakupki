"""Реализации провайдеров данных ЕИС.

Каждый провайдер — подкласс `BaseProvider` (см. `base.py`).

    eis_public — публичные страницы и XML-печатные формы zakupki.gov.ru
    damia      — DaMIA API-Закупки (отключён по умолчанию, нужен ключ)
    gosplan    — ГосПлан API v2 (нужен ключ)
    navodki    — navodki.ru (нужен ключ)
    eis_official — SOAP ЕИС (extra `[eis-official]`, нужен токен)

Цепочка fallback'а собирается в `ProviderResolver` (см. `resolver.py`).
"""

from .base import BaseProvider, ProviderCapability
from .damia import DamiaProvider
from .eis_public import EisPublicProvider
from .gosplan import GosplanProvider
from .navodki import NavodkiProvider
from .resolver import ProviderResolver

__all__ = [
    "BaseProvider",
    "ProviderCapability",
    "DamiaProvider",
    "EisPublicProvider",
    "GosplanProvider",
    "NavodkiProvider",
    "ProviderResolver",
]
