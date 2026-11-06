"""Инспекция XML-путей в фикстурах ЕИС.

Выводит для заданных тегов:
  - полный XPath-подобный путь;
  - текстовое значение;
  - родительский элемент;
  - соседние элементы (предыдущий и следующий по local-name).
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "eis"

_NS_RE = re.compile(r"\{(.*?)\}")


def _q(tag: str) -> str:
    return _NS_RE.sub("", tag)


def _path(el: ET.Element) -> str:
    parts = []
    cur: ET.Element | None = el
    while cur is not None:
        parts.append(_q(cur.tag))
        cur = cur.getparent() if hasattr(cur, "getparent") else None
    # ElementTree doesn't have getparent by default; rebuild manually below
    return "/".join(reversed(parts))


def _build_parent_map(root: ET.Element) -> dict[int, ET.Element]:
    parent_map: dict[int, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parent_map[id(child)] = parent
    return parent_map


def inspect_tag(root: ET.Element, tag_name: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    parent_map = _build_parent_map(root)

    for el in root.iter():
        if _q(el.tag) != tag_name:
            continue

        # Build full path manually
        path_parts = []
        cur: ET.Element | None = el
        while cur is not None:
            path_parts.append(_q(cur.tag))
            cur = parent_map.get(id(cur))
        path = "/".join(reversed(path_parts))

        parent = parent_map.get(id(el))
        parent_tag = _q(parent.tag) if parent is not None else None

        # Siblings: previous and next elements with same parent
        siblings: list[str] = []
        if parent is not None:
            children = list(parent)
            idx = children.index(el)
            if idx > 0:
                siblings.append(f"prev:{_q(children[idx - 1].tag)}")
            if idx < len(children) - 1:
                siblings.append(f"next:{_q(children[idx + 1].tag)}")

        results.append(
            {
                "tag": tag_name,
                "path": path,
                "value": (el.text or "").strip()[:200],
                "parent": parent_tag or "root",
                "siblings": "; ".join(siblings) if siblings else "—",
            }
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Инспекция XML-путей в фикстуре ЕИС")
    parser.add_argument("fixture", help="Имя файла фикстуры (например, 0139300020926000165.xml)")
    parser.add_argument(
        "--tags",
        default="endDate,endDateTime,applicationDeadline,publishDate,publicationDateTime,contractExecution,executionPeriod,startDate",
        help="Список тегов через запятую",
    )
    args = parser.parse_args()

    fixture_path = FIXTURES_DIR / args.fixture
    if not fixture_path.exists():
        print(f"Фикстура не найдена: {fixture_path}", file=sys.stderr)
        sys.exit(1)

    tree = ET.parse(fixture_path)
    root = tree.getroot()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    for tag in tags:
        matches = inspect_tag(root, tag)
        if not matches:
            print(f"\n=== Тег '{tag}': не найден ===")
            continue
        print(f"\n=== Тег '{tag}' (найдено: {len(matches)}) ===")
        for m in matches:
            print(f"  path:        {m['path']}")
            print(f"  value:       {m['value']}")
            print(f"  parent:      {m['parent']}")
            print(f"  siblings:    {m['siblings']}")
            print()


if __name__ == "__main__":
    main()