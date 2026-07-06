"""Реализации провайдеров данных ЕИС.

Каждый провайдер — подкласс `BaseProvider` (см. `base.py`). Open-клиент
v0.1.1+ поддерживает только API-источники (BYOK):

    damia    — DaMIA API-Закупки (commercial)
    gosplan  — ГосПлан API v2 (commercial / sandbox)
    navodki  — navodki.ru (REST поверх ЕИС)
    eis_official — официальный SOAP-сервис ЕИС (extra `[eis-official]`)

HTML-scraping (`html_fallback`) удалён из open-клиента — только hosted Pro
на private `-server/` (v0.2).

Цепочка fallback'а собирается в `ProviderResolver` (см. `resolver.py`).
"""

from .base import BaseProvider, ProviderCapability
from .damia import DamiaProvider
from .gosplan import GosplanProvider
from .navodki import NavodkiProvider
from .resolver import ProviderResolver

__all__ = [
    "BaseProvider",
    "ProviderCapability",
    "DamiaProvider",
    "GosplanProvider",
    "NavodkiProvider",
    "ProviderResolver",
]
