"""Smoke-тесты `lookup_okpd2` — работает локально по vendored seed."""

from __future__ import annotations

import pytest

from mcp_zakupki.context import ServiceContext
from mcp_zakupki.errors import ValidationError
from mcp_zakupki.tools import lookup_okpd2


@pytest.mark.asyncio
class TestLookupOkpd2:
    async def test_finds_dev_okpd_by_keyword(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            result = await lookup_okpd2(ctx, query="разработке программного обеспечения")
        assert result.results, "ожидаем хотя бы 1 совпадение из seed"
        codes = [e.code for e in result.results]
        assert any(c.startswith("62") for c in codes)

    async def test_finds_by_code_prefix(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            result = await lookup_okpd2(ctx, query="62.01")
        assert any(e.code.startswith("62.01") for e in result.results)

    async def test_limit_respected(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            result = await lookup_okpd2(ctx, query="услуги", limit=3)
        assert len(result.results) <= 3

    async def test_empty_query_raises(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            with pytest.raises(ValidationError):
                await lookup_okpd2(ctx, query="")

    async def test_short_query_raises(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            with pytest.raises(ValidationError):
                await lookup_okpd2(ctx, query="ab")

    async def test_invalid_code_type_raises(self, app_config) -> None:
        async with ServiceContext(app_config) as ctx:
            with pytest.raises(ValidationError):
                await lookup_okpd2(ctx, query="разработка", code_type="bad")
