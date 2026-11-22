"""Подтверждённые ограничения текущего HTML-поиска ЕИС и hosted /search."""

from __future__ import annotations

import re
from typing import Any

from .errors import ValidationError
from .schemas import LawType, TenderSearchFilter

PAGE_SIZES = (10, 20, 50)
MAX_PAGE = 2_147_483_647
_UNSUPPORTED = (
    "okpd2_codes", "ktru_codes", "customer_ogrn", "platform", "status", "smp_only",
    "publish_date_from", "publish_date_to",
    "applications_deadline_from", "applications_deadline_to",
)


def validate_public_search(filter_: TenderSearchFilter, *, hosted: bool = False) -> int:
    """Отклонить непередаваемые фильтры до HTTP; вернуть номер страницы ЕИС."""
    unsupported = [name for name in _UNSUPPORTED if getattr(filter_, name)]
    unsupported.extend(
        name for name in ("price_min_rub", "price_max_rub") if getattr(filter_, name) is not None
    )
    if hosted and LawType.PP_615 in filter_.law_type:
        unsupported.append("law_type")
    if unsupported:
        raise ValidationError(
            "Текущий поиск ЕИС не поддерживает указанные фильтры.",
            details={"unsupported_filters": unsupported},
        )
    if filter_.query and filter_.customer_inn:
        raise ValidationError(
            "Поиск ЕИС принимает текст или ИНН заказчика. Укажите один из этих фильтров.",
            details={"fields": ["query", "customer_inn"]},
        )
    for region in filter_.regions:
        validate_region_code(region)
    if filter_.limit not in PAGE_SIZES:
        raise ValidationError(
            "Размер страницы ЕИС должен быть 10, 20 или 50.",
            details={"field": "limit", "allowed": list(PAGE_SIZES)},
        )
    token = filter_.next_page_token
    if token is None:
        return 1
    if not re.fullmatch(r"[1-9][0-9]{0,9}", token) or int(token) > MAX_PAGE:
        raise ValidationError(
            "Токен страницы ЕИС — целое число от 1 до 2147483647 без пробелов и ведущих нулей.",
            details={"field": "next_page_token"},
        )
    return int(token)


def validate_region_code(region: str) -> str:
    """Код региона как строка из двух цифр, например «05» или «66»."""
    normalized = region.strip()
    if not re.fullmatch(r"[0-9]{1,2}", normalized) or normalized in {"0", "00"}:
        raise ValidationError(
            f"Код региона должен быть числом от 01 до 99. Получено: {normalized!r}.",
            details={"region": normalized},
        )
    return normalized.zfill(2)


def hosted_search_payload(filter_: TenderSearchFilter) -> dict[str, Any]:
    """Согласовать имена MCP-фильтров с существующим REST /search."""
    page = validate_public_search(filter_, hosted=True)
    payload = {
        "query": filter_.query,
        "customer_inn": filter_.customer_inn,
        "page": page,
        "page_size": filter_.limit,
    }
    if filter_.regions:
        payload["region_codes"] = [validate_region_code(region) for region in filter_.regions]
    if filter_.law_type and set(filter_.law_type) != {LawType.FZ_44, LawType.FZ_223}:
        payload["law_type"] = filter_.model_dump(mode="json")["law_type"]
    return payload
