"""Скачивание XML-печати формы извещения ЕИС в tests/fixtures/eis/.

Использует только публичную страницу zakupki.gov.ru (viewXml.html).
Не требует токена машинного обмена.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "eis"

_VIEW_XML = "https://zakupki.gov.ru/epz/order/notice/printForm/viewXml.html"


def download_fixture(reg_number: str, *, force: bool = False) -> Path:
    """Скачать XML для reg_number и сохранить в фикстуры.

    Возвращает путь к сохранённому файлу.
    """
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FIXTURES_DIR / f"{reg_number}.xml"

    if out_path.exists() and not force:
        print(f"Фикстура уже существует: {out_path} (используйте --force для перезаписи)")
        return out_path

    print(f"Скачивание {reg_number} из ЕИС...")
    resp = httpx.get(
        _VIEW_XML,
        params={"regNumber": reg_number},
        timeout=30.0,
        headers={"User-Agent": "mcp-zakupki-fixture-downloader/0.1"},
        verify=False,
    )
    resp.raise_for_status()

    text = resp.text
    if not text.strip().lstrip().startswith("<?xml") and "<html" in text[:200].lower():
        print(f"Предупреждение: для {reg_number} получен HTML вместо XML (возможно, 404 или капча)", file=sys.stderr)

    out_path.write_text(text, encoding="utf-8")
    print(f"Сохранено: {out_path} ({len(text)} байт)")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Скачать XML-фикстуру ЕИС по reg_number")
    parser.add_argument("reg_number", help="Реестровый номер закупки")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перезаписать существующую фикстуру",
    )
    args = parser.parse_args()

    try:
        download_fixture(args.reg_number, force=args.force)
    except httpx.HTTPError as exc:
        print(f"Ошибка HTTP при скачивании {args.reg_number}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()