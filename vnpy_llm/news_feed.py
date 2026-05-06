from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .base import NewsItem


def load_news_file(path: str | Path) -> list[NewsItem]:
    file_path = Path(path)
    if not file_path.exists():
        return []

    suffix = file_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        rows: list[dict[str, Any]] = data if isinstance(data, list) else data.get("items", [])
    elif suffix == ".jsonl":
        rows = [json.loads(line) for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    elif suffix == ".csv":
        with file_path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        text = file_path.read_text(encoding="utf-8")
        rows = [{"title": file_path.stem, "content": text, "source": "local_file"}]

    return [NewsItem.from_dict(row) for row in rows]


def load_news_files(paths: list[str | Path]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for path in paths:
        items.extend(load_news_file(path))
    items.sort(key=lambda item: item.source_time)
    return items
