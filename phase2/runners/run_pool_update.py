"""US Multi-Symbol Quant Phase 2 v2 — pool weekly update runner (DRY-RUN by default).

Pipeline:
  1) Read ``phase2/strategy/config/pool_universe.yaml`` (candidate pool).
  2) Read existing ``phase2/strategy/config/pool_config.yaml`` (live pool).
  3) Apply runtime filters via :func:`pool_loader.apply_runtime_filters`
     using a market snapshot built from local YAML / JSON metric files
     (NEVER from a live feed — phase ② v2 forbids network calls here).
  4) Apply 3 safety gates:
       - new pool size > max_pool_size  → exit 2
       - sector concentration > 40%     → exit 6
       - total add+remove > 30% of size → exit 5
  5) Print a unified diff (dry-run) OR overwrite pool_config.yaml + append
     to ``state/pool_update_log.jsonl`` and the project change log
     (``--apply --confirm`` required).

Hard constraints (project rule 2):
  - Default mode is ``--dry-run`` (no file writes).
  - ``--apply`` alone exits 7 with a reminder; require ``--confirm`` too.
  - Network calls forbidden; any data must come from local files.
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from phase2.strategy.pool_loader import (  # noqa: E402
    POOL_HARD_CAP,
    PoolConfig,
    PoolConfigError,
    PoolSymbol,
    apply_runtime_filters,
    append_pool_change_log,
    filter_atr_pct,
    filter_earnings_freeze,
    filter_liquidity,
    filter_price_band,
    load_pool_config,
)

logger = logging.getLogger("run_pool_update")

# Exit codes.
EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_OVER_CHANGE = 5
EXIT_SECTOR_CAP = 6
EXIT_NEED_CONFIRM = 7

DEFAULT_UNIVERSE = _REPO_ROOT / "phase2" / "strategy" / "config" / "pool_universe.yaml"
DEFAULT_LIVE_POOL = _REPO_ROOT / "phase2" / "strategy" / "config" / "pool_config.yaml"
DEFAULT_METRICS = _REPO_ROOT / "phase2" / "strategy" / "config" / "pool_metrics_snapshot.json"
DEFAULT_EARNINGS = _REPO_ROOT / "phase2" / "strategy" / "config" / "earnings_calendar.json"
DEFAULT_LOG = _REPO_ROOT / "state" / "pool_update_log.jsonl"

MAX_CHANGE_RATIO = 0.30
MAX_SECTOR_RATIO = 0.40


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Weekly pool updater for phase ② v2 (offline, dry-run by default).",
    )
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE),
                   help="path to pool_universe.yaml")
    p.add_argument("--live-pool", default=str(DEFAULT_LIVE_POOL),
                   help="path to live pool_config.yaml")
    p.add_argument("--metrics", default=str(DEFAULT_METRICS),
                   help="path to pool_metrics_snapshot.json (offline metric feed)")
    p.add_argument("--earnings", default=str(DEFAULT_EARNINGS),
                   help="path to earnings_calendar.json")
    p.add_argument("--log", default=str(DEFAULT_LOG),
                   help="path to pool_update_log.jsonl")
    p.add_argument("--change-log", default=str(_REPO_ROOT / "docs" / "project_operation_log.md"),
                   help="path to project_operation_log.md (or test sandbox path)")
    p.add_argument("--today", default=None,
                   help="ISO date for earnings freeze evaluation (default: today)")
    p.add_argument("--max-change-ratio", type=float, default=MAX_CHANGE_RATIO,
                   help="reject when (add+remove)/size > this ratio (default 0.30)")
    p.add_argument("--max-sector-ratio", type=float, default=MAX_SECTOR_RATIO,
                   help="reject when any sector ratio > this (default 0.40)")

    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", dest="dry_run", action="store_true",
                     help="default mode: print diff only")
    grp.add_argument("--apply", dest="apply", action="store_true",
                     help="overwrite pool_config.yaml (requires --confirm)")
    p.add_argument("--confirm", action="store_true",
                   help="explicit confirmation; must accompany --apply")
    p.set_defaults(dry_run=True, apply=False)
    return p.parse_args(argv)


def _load_universe_loose(path: Path) -> PoolConfig:
    """Load the candidate universe YAML without enforcing POOL_HARD_CAP.

    The candidate pool is *intentionally* allowed to be larger than the
    live ``max_pool_size`` so the weekly filter has room to pick. We still
    validate currency / required symbol fields / OTC / dedupe via a small
    inline pass that mirrors :func:`pool_loader._validate` minus the size
    cap check.
    """

    if not path.is_file():
        raise FileNotFoundError("universe yaml not found: {p}".format(p=path))
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError("universe yaml root must be a mapping.")
    currency = str(raw.get("currency", "")).strip().upper()
    if currency != "USD":
        raise ValueError("universe currency must be USD; got {c!r}.".format(c=currency))
    raw_syms = raw.get("symbols")
    if not isinstance(raw_syms, list) or not raw_syms:
        raise ValueError("universe symbols must be a non-empty list.")
    seen = set()
    syms: List[PoolSymbol] = []
    for idx, item in enumerate(raw_syms):
        if not isinstance(item, dict):
            raise ValueError("symbols[{i}] must be a mapping.".format(i=idx))
        for f in ("symbol", "market", "market_cap_bucket", "sector"):
            if f not in item:
                raise ValueError("symbols[{i}] missing field {f}.".format(i=idx, f=f))
        sym = str(item["symbol"]).strip().upper()
        if not sym or sym in seen:
            raise ValueError("symbols[{i}] empty or duplicate: {s}.".format(i=idx, s=sym))
        seen.add(sym)
        syms.append(PoolSymbol(
            symbol=sym,
            market=str(item["market"]).strip().upper(),
            market_cap_bucket=str(item["market_cap_bucket"]).strip().lower(),
            sector=str(item["sector"]).strip(),
        ))
    raw_defaults = raw.get("defaults") or {}
    from phase2.strategy.pool_loader import PoolDefaults  # local import to keep top tidy
    defaults = PoolDefaults(
        liquidity_min_adv60_usd=float(raw_defaults.get("liquidity_min_adv60_usd", 50_000_000.0)),
        price_min=float(raw_defaults.get("price_min", 5.0)),
        price_max=float(raw_defaults.get("price_max", 800.0)),
        atr_pct_max=float(raw_defaults.get("atr_pct_max", 0.08)),
        earnings_freeze_days=int(raw_defaults.get("earnings_freeze_days", 2)),
        market=str(raw_defaults.get("market", "US")).strip().upper(),
    )
    return PoolConfig(
        version=int(raw.get("version", 1)),
        currency=currency,
        max_pool_size=int(raw.get("max_pool_size", POOL_HARD_CAP)),
        defaults=defaults,
        symbols=syms,
    )


def _filter_universe(
    cfg: PoolConfig,
    *,
    market_snapshot: Dict[str, Dict[str, float]],
    earnings_calendar: Optional[Dict[str, List[str]]],
    today_iso: str,
) -> Tuple[List[str], Dict[str, str]]:
    """Same 4 filters as :func:`apply_runtime_filters` but works on a >20
    universe (the upstream helper is bounded by POOL_HARD_CAP)."""

    admitted: List[str] = []
    rejected: Dict[str, str] = {}
    d = cfg.defaults
    for s in cfg.symbols:
        snap = market_snapshot.get(s.symbol)
        if not snap:
            rejected[s.symbol] = "no_snapshot"
            continue
        ok, why = filter_liquidity(float(snap.get("adv60_usd", -1.0)),
                                   d.liquidity_min_adv60_usd)
        if not ok:
            rejected[s.symbol] = why
            continue
        ok, why = filter_price_band(float(snap.get("price", -1.0)),
                                    d.price_min, d.price_max)
        if not ok:
            rejected[s.symbol] = why
            continue
        ok, why = filter_atr_pct(float(snap.get("atr_pct", -1.0)), d.atr_pct_max)
        if not ok:
            rejected[s.symbol] = why
            continue
        if earnings_calendar is not None:
            ok, why = filter_earnings_freeze(
                s.symbol, today_iso, earnings_calendar, d.earnings_freeze_days
            )
            if not ok:
                rejected[s.symbol] = why
                continue
        admitted.append(s.symbol)
    return admitted, rejected


def _load_metrics(path: Path) -> Dict[str, Dict[str, float]]:
    """Load offline metric snapshot.

    Schema: ``{ "AAPL": {"price": 192.0, "adv60_usd": 3.5e10, "atr_pct": 0.018}, ... }``

    Missing file → empty dict (every symbol will fail "no_snapshot" filter,
    which we report cleanly upstream).
    """

    if not path.is_file():
        logger.warning("metrics snapshot not found: %s; treating all as no_snapshot.", path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            "[run_pool_update] FAIL: cannot parse metrics file {p}: {e}".format(
                p=path, e=exc
            )
        )


def _load_earnings(path: Path) -> Dict[str, List[str]]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            out: Dict[str, List[str]] = {}
            for k, v in raw.items():
                # Skip metadata keys (e.g. ``_schema_version``) and any
                # non-list value — only iterate symbol → list[str] entries.
                if not isinstance(v, list):
                    continue
                out[str(k).upper()] = [str(x) for x in v]
            return out
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _check_sector_cap(symbols: List[Dict[str, str]], max_ratio: float) -> Tuple[bool, str]:
    if not symbols:
        return True, ""
    counts = Counter(s["sector"] for s in symbols)
    total = len(symbols)
    for sector, c in counts.items():
        if c / total > max_ratio:
            return False, "sector_cap:{s}={r:.2%}".format(
                s=sector, r=c / total
            )
    return True, ""


def _diff_yaml(old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="pool_config.yaml (current)",
            tofile="pool_config.yaml (proposed)",
            n=3,
        )
    )


def _render_yaml(symbols: List[Dict[str, str]], live_cfg: PoolConfig) -> str:
    """Render the new pool_config.yaml string deterministically.

    We hand-render rather than ``yaml.safe_dump`` so the output stays
    aligned with the existing file's commented header + ordering.
    """

    d = live_cfg.defaults
    lines: List[str] = []
    lines.append("# 美股多标的量化策略 阶段 ② 标的池配置")
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("# 维护说明：")
    lines.append("# 1) 池规模硬上限 = 20，加载阶段 (pool_loader.py) 会做 schema 校验。")
    lines.append("# 2) 每条 symbol 必须包含：market / market_cap_bucket / sector 三个字段。")
    lines.append("# 3) 仅允许币种 USD；OTC、IPO < 1 年的 symbol 禁止入池。")
    lines.append("# 4) 池变更（增删 symbol）必须在 docs/project_operation_log.md 同步追加一条记录。")
    lines.append("# 5) 本文件由 phase2/runners/run_pool_update.py 周度生成；手工修改请同步候选池。")
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("")
    lines.append("version: {v}".format(v=live_cfg.version))
    lines.append("currency: {c}".format(c=live_cfg.currency))
    lines.append("max_pool_size: {m}".format(m=live_cfg.max_pool_size))
    lines.append("")
    lines.append("defaults:")
    lines.append("  market: {m}".format(m=d.market))
    lines.append("  liquidity_min_adv60_usd: {v:.0f}".format(v=d.liquidity_min_adv60_usd))
    lines.append("  price_min: {v}".format(v=d.price_min))
    lines.append("  price_max: {v}".format(v=d.price_max))
    lines.append("  atr_pct_max: {v}".format(v=d.atr_pct_max))
    lines.append("  earnings_freeze_days: {v}".format(v=d.earnings_freeze_days))
    lines.append("")
    lines.append("symbols:")
    for s in symbols:
        lines.append("  - symbol: {sy}".format(sy=s["symbol"]))
        lines.append("    market: {m}".format(m=s["market"]))
        lines.append("    market_cap_bucket: {b}".format(b=s["market_cap_bucket"]))
        lines.append("    sector: {se}".format(se=s["sector"]))
    lines.append("")
    return "\n".join(lines)


def _append_jsonl(log_path: Path, entry: Dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    args = _parse_args(argv)

    if args.apply and not args.confirm:
        print("[run_pool_update] ERROR: --apply requires --confirm "
              "(per project rule 2).", file=sys.stderr)
        return EXIT_NEED_CONFIRM

    universe_path = Path(args.universe)
    live_path = Path(args.live_pool)
    metrics_path = Path(args.metrics)
    earnings_path = Path(args.earnings)
    log_path = Path(args.log)
    today_iso = args.today or date.today().isoformat()

    try:
        universe_cfg = _load_universe_loose(universe_path)
    except (PoolConfigError, FileNotFoundError, ValueError) as exc:
        print("[run_pool_update] FAIL: universe schema invalid: {e}".format(e=exc),
              file=sys.stderr)
        return EXIT_VALIDATION
    try:
        live_cfg = load_pool_config(live_path)
    except (PoolConfigError, FileNotFoundError) as exc:
        print("[run_pool_update] FAIL: live pool_config invalid: {e}".format(e=exc),
              file=sys.stderr)
        return EXIT_VALIDATION

    metrics = _load_metrics(metrics_path)
    earnings = _load_earnings(earnings_path)

    admitted, rejected = _filter_universe(
        universe_cfg,
        market_snapshot=metrics,
        earnings_calendar=earnings or None,
        today_iso=today_iso,
    )

    # Build admitted list back into structured dicts (preserve metadata).
    by_sym = {s.symbol: s for s in universe_cfg.symbols}
    admitted_full = [
        {
            "symbol": sym,
            "market": by_sym[sym].market,
            "market_cap_bucket": by_sym[sym].market_cap_bucket,
            "sector": by_sym[sym].sector,
        }
        for sym in admitted
        if sym in by_sym
    ]

    # Cap by max_pool_size (prefer mega > large > mid > small, then ADV).
    bucket_rank = {"mega": 0, "large": 1, "mid": 2, "small": 3}
    admitted_full.sort(key=lambda s: (bucket_rank.get(s["market_cap_bucket"], 9),
                                      s["symbol"]))
    admitted_full = admitted_full[: live_cfg.max_pool_size]

    # ---- Safety gates ----
    if len(admitted_full) > POOL_HARD_CAP:
        print("[run_pool_update] FAIL: new pool size {n} exceeds hard cap {c}.".format(
            n=len(admitted_full), c=POOL_HARD_CAP), file=sys.stderr)
        return EXIT_VALIDATION

    sec_ok, sec_reason = _check_sector_cap(admitted_full, args.max_sector_ratio)
    if not sec_ok:
        print("[run_pool_update] FAIL: {r}.".format(r=sec_reason), file=sys.stderr)
        return EXIT_SECTOR_CAP

    old_syms = set(s.symbol for s in live_cfg.symbols)
    new_syms = set(s["symbol"] for s in admitted_full)
    added = sorted(new_syms - old_syms)
    removed = sorted(old_syms - new_syms)
    base_size = max(1, len(old_syms))
    change_ratio = (len(added) + len(removed)) / base_size

    # ---- Diff & report ----
    old_text = live_path.read_text(encoding="utf-8") if live_path.is_file() else ""
    new_text = _render_yaml(admitted_full, live_cfg)
    diff_text = _diff_yaml(old_text, new_text)

    print("[run_pool_update] universe={uc} symbols, admitted={ac}, rejected={rc}.".format(
        uc=len(universe_cfg.symbols), ac=len(admitted_full), rc=len(rejected)))
    print("[run_pool_update] add={a} remove={r} change_ratio={cr:.2%}.".format(
        a=added, r=removed, cr=change_ratio))
    if rejected:
        print("[run_pool_update] reject sample (first 5): {s}".format(
            s=list(rejected.items())[:5]))

    if change_ratio > args.max_change_ratio:
        # In dry-run we still show the diff for human review, but signal
        # via exit code so CI catches the over-change before --apply.
        if args.dry_run and not args.apply:
            print("[run_pool_update] WARN: change ratio {cr:.2%} > limit {lm:.2%} "
                  "(dry-run continues; --apply would refuse).".format(
                      cr=change_ratio, lm=args.max_change_ratio), file=sys.stderr)
            if diff_text:
                print(diff_text)
            else:
                print("[run_pool_update] (no changes)")
            return EXIT_OVER_CHANGE
        print("[run_pool_update] FAIL: change ratio {cr:.2%} > limit {lm:.2%}.".format(
            cr=change_ratio, lm=args.max_change_ratio), file=sys.stderr)
        return EXIT_OVER_CHANGE

    if args.dry_run and not args.apply:
        print("[run_pool_update] DRY-RUN — no files written.")
        if diff_text:
            print(diff_text)
        else:
            print("[run_pool_update] (no changes)")
        return EXIT_OK

    # ---- Apply path ----
    live_path.write_text(new_text, encoding="utf-8")
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": today_iso,
        "added": added,
        "removed": removed,
        "change_ratio": round(change_ratio, 4),
        "size": len(admitted_full),
        "rejected_count": len(rejected),
    }
    _append_jsonl(log_path, entry)
    if added or removed:
        try:
            append_pool_change_log(
                Path(args.change_log),
                added=added, removed=removed,
                reason="weekly run_pool_update", date_iso=today_iso,
            )
        except Exception as exc:  # pragma: no cover - best-effort
            print("[run_pool_update] WARN: append_pool_change_log failed: {e}".format(
                e=exc), file=sys.stderr)
    print("[run_pool_update] APPLIED: wrote {p}".format(p=live_path))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
