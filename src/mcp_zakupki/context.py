"""Сервис-контекст: единый holder для cache + providers + config.

Создаётся лениво в `server.py` при первом MCP-вызове. Закрывается через
`atexit`-хук (чтобы не висели открытые httpx-клиенты). Шаблон взят из
эталонного `mcp-fns-check-client/.../context.py`.
"""

from __future__ import annotations

import logging
from typing import Any

from .cache import CacheStore
from .config import AppConfig
from .providers import ProviderResolver

logger = logging.getLogger(__name__)


class ServiceContext:
    """Зависимости тулзов на жизненном цикле процесса."""

    def __init__(
        self,
        config: AppConfig,
        *,
        cache: CacheStore | None = None,
        resolver: ProviderResolver | None = None,
    ) -> None:
        self.config = config
        self.cache = cache or CacheStore(config.cache_db)
        self.resolver = resolver or ProviderResolver(config)

    @classmethod
    def from_env(cls) -> ServiceContext:
        return cls(AppConfig.from_env())

    async def __aenter__(self) -> ServiceContext:
        await self.cache.init()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.resolver.aclose()
        await self.cache.close()
