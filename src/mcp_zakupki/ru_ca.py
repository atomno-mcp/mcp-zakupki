"""Официальные корневые сертификаты Минцифры для площадок вроде zakupki.gov.ru.

Сайт госзакупок предъявляет сертификат «Russian Trusted Sub CA». Этого
центра нет в стандартном наборе certifi, поэтому обычный httpx обрывается
на проверке цепочки. Установка официальных корней — обычное доверие,
не обход защиты.
"""

from __future__ import annotations

import ssl
from functools import lru_cache
from importlib import resources


def _extra_pems() -> str:
    return resources.files("mcp_zakupki").joinpath("data/russian_trusted_ca.pem").read_text(
        encoding="ascii"
    )


@lru_cache(maxsize=1)
def ssl_context_with_mintcifry() -> ssl.SSLContext:
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=_extra_pems())
    return ctx
