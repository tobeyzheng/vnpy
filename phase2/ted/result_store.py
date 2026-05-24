from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

DEFAULT_BASE_PATH = "/projects/vnpy"
DEFAULT_RUNS_ROOT = "state/runs/ted"
DEFAULT_COMPAT_REPORT = "state/runs/ted_aggressive_pool_report.json"
DEFAULT_COMPAT_POOL_CONFIG = "state/runs/ted_pool_config.yaml"

_THEME_TO_SECTOR = {
    "ai_compute": "Technology",
    "platform_internet": "Technology",
    "consumer_growth": "ConsumerDiscretionary",
    "financial_defensive": "Financials",
    "general": "Technology",
}


def resolve_requested_run_dir(run_dir: str | None = None) -> str | None:
    return run_dir or os.environ.get("TED_RUN_DIR") or None



def build_default_run_id(now: datetime | None = None) -> str:
    current = now or datetime.now()
    return current.strftime("%Y%m%dT%H%M%S")



def resolve_ted_run_dir(
    run_dir: str | None = None,
    *,
    base_path: str = DEFAULT_BASE_PATH,
    create: bool = True,
) -> Path:
    requested = resolve_requested_run_dir(run_dir)
    if requested:
        path = Path(requested)
    else:
        path = Path(base_path) / DEFAULT_RUNS_ROOT / build_default_run_id()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path



def ensure_stage_dir(run_dir: str | Path, stage_name: str) -> Path:
    path = Path(run_dir) / stage_name
    path.mkdir(parents=True, exist_ok=True)
    return path



def stage_file_path(
    run_dir: str | Path,
    stage_name: str,
    filename: str,
    *,
    create_parent: bool = True,
) -> Path:
    stage_dir = ensure_stage_dir(run_dir, stage_name) if create_parent else Path(run_dir) / stage_name
    return stage_dir / filename



def copy_artifact(source_path: str | Path, destination_path: str | Path) -> Path:
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"artifact not found: {src}")
    dst = Path(destination_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst



def write_json(path: str | Path, payload: Dict[str, Any]) -> Path:
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst



def write_text(path: str | Path, content: str) -> Path:
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8")
    return dst



def update_run_manifest(run_dir: str | Path, **entries: Any) -> Path:
    manifest_path = Path(run_dir) / "manifest.json"
    existing: Dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}

    manifest = {
        "schema_version": "ted_result_manifest_v1",
        "run_dir": str(Path(run_dir)),
        "updated_at": datetime.now().isoformat(),
    }
    if isinstance(existing, dict):
        manifest.update(existing)
    for key, value in entries.items():
        manifest[key] = value

    return write_json(manifest_path, manifest)



def normalize_pool_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if "." in text:
        return text.rsplit(".", 1)[0]
    return text



def infer_market_from_symbol(symbol: str) -> str:
    suffix = str(symbol or "").strip().upper().rsplit(".", 1)[-1]
    if suffix == "HK":
        return "HK"
    return "US"



def infer_market_cap_bucket(market_cap: float | int | None) -> str:
    try:
        cap = float(market_cap or 0)
    except (TypeError, ValueError):
        cap = 0.0
    if cap >= 200_000_000_000:
        return "mega"
    if cap >= 10_000_000_000:
        return "large"
    if cap >= 2_000_000_000:
        return "mid"
    return "small"



def infer_sector(candidate: Dict[str, Any]) -> str:
    sector = str(candidate.get("sector") or "").strip()
    if sector:
        return sector
    quote = candidate.get("quote") or {}
    quote_sector = str(quote.get("sector") or quote.get("industry") or "").strip()
    if quote_sector:
        return quote_sector
    theme_bucket = str(candidate.get("theme_bucket") or "").strip().lower()
    return _THEME_TO_SECTOR.get(theme_bucket, "Technology")



def _iter_unique_candidates(categorized_pool: Dict[str, List[Dict[str, Any]]]) -> Iterable[Dict[str, Any]]:
    seen: set[str] = set()
    for pool_name in ("core_pool", "watch_pool", "reserve_pool"):
        for candidate in categorized_pool.get(pool_name, []) or []:
            symbol = str(candidate.get("symbol") or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            yield candidate



def render_pool_config_yaml(
    categorized_pool: Dict[str, List[Dict[str, Any]]],
    *,
    version: int = 1,
    currency: str = "USD",
    max_pool_size: int = 20,
    defaults: Dict[str, Any] | None = None,
) -> str:
    default_values = {
        "market": "US",
        "liquidity_min_adv60_usd": 50_000_000,
        "price_min": 5.0,
        "price_max": 800.0,
        "atr_pct_max": 0.08,
        "earnings_freeze_days": 2,
    }
    if defaults:
        default_values.update(defaults)

    rows: List[Dict[str, str]] = []
    for candidate in _iter_unique_candidates(categorized_pool):
        quote = candidate.get("quote") or {}
        symbol = str(candidate.get("symbol") or "").strip().upper()
        rows.append(
            {
                "symbol": normalize_pool_symbol(symbol),
                "market": infer_market_from_symbol(symbol),
                "market_cap_bucket": infer_market_cap_bucket(quote.get("market_cap")),
                "sector": infer_sector(candidate),
            }
        )

    bucket_rank = {"mega": 0, "large": 1, "mid": 2, "small": 3}
    rows.sort(key=lambda item: (bucket_rank.get(item["market_cap_bucket"], 9), item["symbol"]))

    lines: List[str] = []
    lines.append("# TED 进攻型标池导出配置")
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("# 维护说明：")
    lines.append("# 1) 本文件由 phase2/ted 自动生成，供 phase2 pool loader 直接复用。")
    lines.append("# 2) 每条 symbol 均包含：market / market_cap_bucket / sector 三个字段。")
    lines.append("# 3) 当前标的是 TED stage3 最终导出结果，按市值桶与代码排序。")
    lines.append("# 4) 如需追溯原始研究与候选，请回看同批次 TED dated run 目录。")
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("")
    lines.append(f"version: {version}")
    lines.append(f"currency: {currency}")
    lines.append(f"max_pool_size: {max_pool_size}")
    lines.append("")
    lines.append("defaults:")
    lines.append(f"  market: {default_values['market']}")
    lines.append(f"  liquidity_min_adv60_usd: {float(default_values['liquidity_min_adv60_usd']):.0f}")
    lines.append(f"  price_min: {default_values['price_min']}")
    lines.append(f"  price_max: {default_values['price_max']}")
    lines.append(f"  atr_pct_max: {default_values['atr_pct_max']}")
    lines.append(f"  earnings_freeze_days: {int(default_values['earnings_freeze_days'])}")
    lines.append("")
    lines.append("symbols:")
    for row in rows:
        lines.append(f"  - symbol: {row['symbol']}")
        lines.append(f"    market: {row['market']}")
        lines.append(f"    market_cap_bucket: {row['market_cap_bucket']}")
        lines.append(f"    sector: {row['sector']}")
    lines.append("")
    return "\n".join(lines)
