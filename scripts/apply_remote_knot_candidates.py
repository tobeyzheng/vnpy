from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.strategy.symbols import normalize_symbol


REQUIRED_FIELDS = {"symbol", "market", "name", "rationale"}


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def extract_items(data: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(data, list):
        rows.extend(item for item in data if isinstance(item, dict))
    elif isinstance(data, dict):
        for key in ("items", "candidates", "results", "remote_result"):
            value = data.get(key)
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
            elif isinstance(value, dict):
                rows.extend(extract_items(value))
        for task in data.get("dynamic_candidate_tasks", []) if isinstance(data.get("dynamic_candidate_tasks"), list) else []:
            rows.extend(extract_items(task.get("remote_result")))
    return rows


def normalize_item(row: dict[str, Any]) -> dict[str, Any] | None:
    if not REQUIRED_FIELDS.issubset(row):
        return None
    market = str(row.get("market") or "")
    symbol = normalize_symbol(str(row.get("symbol") or ""), market)
    if not market or not symbol:
        return None
    return {
        "symbol": symbol,
        "market": market,
        "name": str(row.get("name") or symbol),
        "rationale": str(row.get("rationale") or ""),
        "risk": str(row.get("risk") or ""),
        "raw_score": float(row.get("raw_score", row.get("score", 0.55)) or 0.55),
        "confidence_source": str(row.get("confidence_source") or "knot_agent_dynamic"),
        "action_hint": str(row.get("action_hint") or "watch_only"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    runs = REPO_ROOT / "state" / "runs"
    sources = [
        runs / "remote_knot_dynamic_candidates.json",
        runs / "remote_knot_batch_tasks.json",
    ]
    items: list[dict[str, Any]] = []
    for path in sources:
        items.extend(extract_items(load_json(path)))
    normalized = [item for item in (normalize_item(row) for row in items) if item]
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for item in normalized:
        dedup[(item["market"], item["symbol"])] = item
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "remote_knot_candidates",
        "items": list(dedup.values()),
    }
    path = runs / "candidate_inputs.dynamic.json"
    if not out["items"] and "--allow-empty" not in sys.argv:
        print(path)
        print(json.dumps({"written": 0, "skipped": True, "reason": "no valid remote candidates"}, ensure_ascii=False))
        return
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
    print(json.dumps({"written": len(out["items"]), "source_files": [p.name for p in sources]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
