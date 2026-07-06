"""Валидаторы для атрибутов российских тендеров (SPEC §5.x).

Все функции возвращают нормализованную строку при успехе и кидают
`ValidationError` при ошибке. Используются в сигнатурах MCP-тулзов
до отправки запросов в провайдеры.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Final

from .errors import ValidationError

_RE_INN_LEGAL: Final[re.Pattern[str]] = re.compile(r"^\d{10}$")
_RE_INN_INDIVIDUAL: Final[re.Pattern[str]] = re.compile(r"^\d{12}$")
_RE_OGRN_LEGAL: Final[re.Pattern[str]] = re.compile(r"^\d{13}$")
_RE_OGRN_INDIVIDUAL: Final[re.Pattern[str]] = re.compile(r"^\d{15}$")
_RE_REG_NUMBER: Final[re.Pattern[str]] = re.compile(r"^\d{19,21}$")
_RE_OKPD2: Final[re.Pattern[str]] = re.compile(r"^\d{2}(\.\d{1,3}){0,5}$")
_RE_REGION_CODE: Final[re.Pattern[str]] = re.compile(r"^\d{1,2}$")


def normalize_str(raw: str | None) -> str | None:
    """Удалить пробелы, привести `""` и `None` к None."""
    if raw is None:
        return None
    s = raw.strip()
    return s or None


def validate_inn(inn: str | None) -> str | None:
    """ИНН: 10 цифр (юр.лицо) или 12 цифр (физлицо/ИП).

    Возвращает нормализованный ИНН (без пробелов) или None если вход пустой.
    Кидает ValidationError при невалидной длине / нецифровых символах.
    """
    norm = normalize_str(inn)
    if norm is None:
        return None
    if not (_RE_INN_LEGAL.match(norm) or _RE_INN_INDIVIDUAL.match(norm)):
        raise ValidationError(
            f"ИНН должен быть 10 цифр (юр.лицо) или 12 цифр (ИП/физлицо). Получено: {norm!r}.",
            details={"inn": norm, "expected_lengths": [10, 12]},
        )
    return norm


def validate_ogrn(ogrn: str | None) -> str | None:
    """ОГРН: 13 цифр (юр.лицо) или 15 цифр (ОГРНИП)."""
    norm = normalize_str(ogrn)
    if norm is None:
        return None
    if not (_RE_OGRN_LEGAL.match(norm) or _RE_OGRN_INDIVIDUAL.match(norm)):
        raise ValidationError(
            f"ОГРН должен быть 13 цифр (юр.лицо) или 15 цифр (ИП). Получено: {norm!r}.",
            details={"ogrn": norm, "expected_lengths": [13, 15]},
        )
    return norm


def validate_reg_number(reg_number: str | None) -> str:
    """Реестровый номер тендера в ЕИС: 19–21 цифр.

    Обязательное поле; пустой вход кидает ValidationError.
    """
    norm = normalize_str(reg_number)
    if norm is None:
        raise ValidationError(
            "Реестровый номер тендера обязателен (поле reg_number).",
            details={"reg_number": None},
        )
    if not _RE_REG_NUMBER.match(norm):
        raise ValidationError(
            f"Реестровый номер ЕИС должен быть 19–21 цифр. Получено: {norm!r}.",
            details={"reg_number": norm, "expected_length": "19-21"},
        )
    return norm


def validate_okpd2_code(code: str) -> str:
    """ОКПД2 — 2 цифры + до 5 групп `.NN` (`62`, `62.0`, `62.01`, `62.01.11.000`)."""
    norm = normalize_str(code)
    if norm is None:
        raise ValidationError(
            "Код ОКПД2 не может быть пустым.",
            details={"code": code},
        )
    if not _RE_OKPD2.match(norm):
        raise ValidationError(
            f"Код ОКПД2 имеет формат 'NN[.NN][.NN]...'. Получено: {norm!r}.",
            details={"code": norm, "pattern": _RE_OKPD2.pattern},
        )
    return norm


def validate_okpd2_codes(codes: list[str] | None) -> list[str]:
    """Список ОКПД2-кодов. Пустой/None → пустой список."""
    if not codes:
        return []
    return [validate_okpd2_code(c) for c in codes]


def validate_region_code(region: str) -> str:
    """Код субъекта РФ — 1–2 цифры (`5`, `74`, `77`, `99`)."""
    norm = normalize_str(region)
    if norm is None:
        raise ValidationError(
            "Код региона не может быть пустым.",
            details={"region": region},
        )
    if not _RE_REGION_CODE.match(norm):
        raise ValidationError(
            f"Код региона должен быть 1–2 цифры. Получено: {norm!r}.",
            details={"region": norm},
        )
    return norm.zfill(2)


def validate_price_range(
    price_min: float | int | None,
    price_max: float | int | None,
) -> tuple[float | None, float | None]:
    """price_min ≤ price_max, оба ≥ 0."""
    if price_min is not None and price_min < 0:
        raise ValidationError(
            f"price_min_rub должен быть ≥ 0. Получено: {price_min}.",
            details={"price_min_rub": price_min},
        )
    if price_max is not None and price_max < 0:
        raise ValidationError(
            f"price_max_rub должен быть ≥ 0. Получено: {price_max}.",
            details={"price_max_rub": price_max},
        )
    if price_min is not None and price_max is not None and price_min > price_max:
        raise ValidationError(
            f"price_min_rub ({price_min}) > price_max_rub ({price_max}).",
            details={"price_min_rub": price_min, "price_max_rub": price_max},
        )
    pmin = float(price_min) if price_min is not None else None
    pmax = float(price_max) if price_max is not None else None
    return pmin, pmax


def validate_date_range(
    date_from: str | None,
    date_to: str | None,
    *,
    field_from: str = "date_from",
    field_to: str = "date_to",
) -> tuple[date | None, date | None]:
    """Распарсить строки YYYY-MM-DD и проверить, что `date_from ≤ date_to`."""
    parsed_from = _parse_date_or_none(date_from, field_from)
    parsed_to = _parse_date_or_none(date_to, field_to)
    if parsed_from is not None and parsed_to is not None and parsed_from > parsed_to:
        raise InvalidFilterRangeError(
            field_from=field_from,
            field_to=field_to,
            value_from=parsed_from.isoformat(),
            value_to=parsed_to.isoformat(),
        )
    return parsed_from, parsed_to


def _parse_date_or_none(raw: str | None, field: str) -> date | None:
    norm = normalize_str(raw)
    if norm is None:
        return None
    try:
        return date.fromisoformat(norm)
    except ValueError as exc:
        raise ValidationError(
            f"{field}: ожидается дата в формате YYYY-MM-DD. Получено: {raw!r}.",
            details={"field": field, "value": raw},
        ) from exc


class InvalidFilterRangeError(ValidationError):
    """Перечисление: `<from> > <to>` для дат / диапазонов."""

    def __init__(
        self,
        *,
        field_from: str,
        field_to: str,
        value_from: str,
        value_to: str,
    ) -> None:
        super().__init__(
            f"{field_from} ({value_from}) > {field_to} ({value_to}).",
            details={
                field_from: value_from,
                field_to: value_to,
            },
        )


def utc_now() -> datetime:
    """Шорткат для меток времени в audit/cache (timezone-aware UTC)."""
    return datetime.now(UTC)
