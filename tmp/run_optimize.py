# -*- coding: utf-8 -*-
"""Reusable multi-interval parameter optimization + OOS validation.

Workflow (per interval):
  1. Load base bars from local vnpy DB (5m / 1h / 1d already pulled).
  2. Resample to target interval if needed (10m, 30m from 5m; 4h from 1h).
  3. In-Sample (IS) brute-force grid search over the strategy parameter space,
     ranked by `sharpe_ratio` with a `total_trade_count` floor.
  4. Take best params, run a clean Out-of-Sample (OOS) backtest on the
     reserved tail window, persist statistics.
  5. Cross-interval summary at the end.

Usage (defaults match the NVDA / strategy_simple_multifactor task)::

    python tmp/run_optimize.py

Override:
    python tmp/run_optimize.py \\
        --strategy tmp/strategy/strategy_simple_multifactor.py \\
        --symbol NVDA.SMART \\
        --intervals 5m,10m,30m,1h,4h,1d \\
        --is-start 2024-01-01 --is-end 2025-11-12 \\
        --oos-start 2025-11-13 --oos-end 2026-05-13 \\
        --capital 10000

Outputs land under `tmp/data/optimize_results/<strategy>_<symbol>_<ts>/`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup so we can import the local adapter without packaging.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TMP_DIR = Path(__file__).resolve().parent
for p in (_PROJECT_ROOT, _TMP_DIR):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData
from vnpy_ctastrategy.backtesting import BacktestingEngine

from strategy_adapter import make_cta_class  # type: ignore  # noqa: E402

logger = logging.getLogger("optimize")

# ---------------------------------------------------------------------------
# Interval spec: how each requested label maps to a base DB interval and
# resample window. ``base`` is what we read from the local SQLite; ``window``
# is "how many base bars per output bar" (1 means no resample).
# ---------------------------------------------------------------------------
@dataclass
class IntervalSpec:
    label: str               # human label, e.g. "10m"
    base_interval: Interval  # vnpy interval present in DB
    window: int              # bars-per-output, 1 = passthrough
    annual_days: int = 240   # used by sharpe / annualization


INTERVAL_SPECS: Dict[str, IntervalSpec] = {
    "5m":  IntervalSpec("5m",  Interval.MINUTE, 1,  annual_days=240),
    "10m": IntervalSpec("10m", Interval.MINUTE, 2,  annual_days=240),
    "30m": IntervalSpec("30m", Interval.MINUTE, 6,  annual_days=240),
    "1h":  IntervalSpec("1h",  Interval.HOUR,   1,  annual_days=240),
    "4h":  IntervalSpec("4h",  Interval.HOUR,   4,  annual_days=240),
    "1d":  IntervalSpec("1d",  Interval.DAILY,  1,  annual_days=240),
}


# ---------------------------------------------------------------------------
# Resampling helper.
# ---------------------------------------------------------------------------
def resample_bars(bars: List[BarData], window: int, label: str) -> List[BarData]:
    """Aggregate ``window`` consecutive bars into one. Open=first.open,
    Close=last.close, High=max, Low=min, Volume=sum, datetime=first.datetime.

    For window==1 the input is returned unchanged.
    """
    if window <= 1:
        return list(bars)
    if not bars:
        return []

    out: List[BarData] = []
    bucket: List[BarData] = []
    for b in bars:
        bucket.append(b)
        if len(bucket) == window:
            agg = BarData(
                symbol=bucket[0].symbol,
                exchange=bucket[0].exchange,
                datetime=bucket[0].datetime,
                interval=bucket[0].interval,  # purely cosmetic
                gateway_name=bucket[0].gateway_name,
                open_price=bucket[0].open_price,
                high_price=max(x.high_price for x in bucket),
                low_price=min(x.low_price for x in bucket),
                close_price=bucket[-1].close_price,
                volume=sum(x.volume for x in bucket),
                turnover=sum(getattr(x, "turnover", 0.0) or 0.0 for x in bucket),
                open_interest=bucket[-1].open_interest,
            )
            out.append(agg)
            bucket = []

    # Drop trailing partial bucket (incomplete window) intentionally.
    logger.info(
        "[resample] %s: %d base bars -> %d aggregated bars (window=%d, drop=%d)",
        label, len(bars), len(out), window, len(bucket),
    )
    return out


def load_bars(symbol: str, exchange: Exchange, interval: Interval,
              start: datetime, end: datetime) -> List[BarData]:
    db = get_database()
    bars = db.load_bar_data(symbol, exchange, interval, start, end)
    return list(bars)


# ---------------------------------------------------------------------------
# Backtest runner: bypasses engine.load_data() and feeds pre-built bars.
# ---------------------------------------------------------------------------
def run_one_backtest(
    *,
    cta_class: type,
    setting: Dict[str, Any],
    vt_symbol: str,
    bars: List[BarData],
    start: datetime,
    end: datetime,
    capital: float,
    annual_days: int,
    rate: float = 0.0,
    slippage: float = 0.0,
    size: float = 1.0,
    pricetick: float = 0.01,
) -> Dict[str, Any]:
    """Run a single backtest and return statistics dict.

    We bypass engine.load_data() by overwriting ``engine.history_data``
    directly; vnpy's run_backtesting iterates over that list as-is.
    """
    engine = BacktestingEngine()
    # vnpy stores bars tz-aware; ensure start/end are tz-aware too.
    tz = bars[0].datetime.tzinfo if bars and bars[0].datetime.tzinfo else None
    if tz is not None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz)
    engine.set_parameters(
        vt_symbol=vt_symbol,
        interval=Interval.MINUTE,  # any value; not used by run_backtesting
        start=start,
        end=end,
        rate=rate,
        slippage=slippage,
        size=size,
        pricetick=pricetick,
        capital=int(capital),
        annual_days=annual_days,
    )
    engine.add_strategy(cta_class, setting)

    # Filter bars to [start, end] window. Normalize tz-awareness so that
    # bars stored with timezone (vnpy default) compare cleanly with naive
    # CLI inputs.
    end_eod = end.replace(hour=23, minute=59, second=59)
    if bars and bars[0].datetime.tzinfo is not None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=bars[0].datetime.tzinfo)
        if end_eod.tzinfo is None:
            end_eod = end_eod.replace(tzinfo=bars[0].datetime.tzinfo)
    filtered = [b for b in bars if start <= b.datetime <= end_eod]
    engine.history_data = filtered

    if not filtered:
        return {
            "trade_count": 0,
            "sharpe_ratio": 0.0,
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "_error": "no bars in window",
            "_bars": 0,
        }

    engine.run_backtesting()
    engine.calculate_result()
    stats = engine.calculate_statistics(output=False)
    if not isinstance(stats, dict):
        stats = {}
    stats["_bars"] = len(filtered)
    return stats


# ---------------------------------------------------------------------------
# Parameter grid: per-interval custom grids.
# Total ~ 5m:96 + 10m:64 + 30m:96 + 1h:96 + 4h:96 + 1d:96 = 544.
# ---------------------------------------------------------------------------
def build_param_grid(label: str) -> List[Dict[str, Any]]:
    """Return a list of param dicts to evaluate for the given interval."""
    if label in ("5m", "10m"):
        # Fast intraday: shorter MAs, tighter SL/TP.
        fast    = [3, 5, 8]
        slow    = [13, 20, 30, 50]
        rsi_w   = [9, 14]
        rsi_os  = [25, 30, 35]
        rsi_ob  = [65, 70, 75]
        vol_th  = [1.0, 1.2, 1.5]
        sl      = [0.02, 0.03, 0.05]
        tp      = [0.05, 0.10, 0.15]
        pos     = [0.30]
    elif label == "30m":
        fast    = [3, 5, 8]
        slow    = [13, 20, 30]
        rsi_w   = [14]
        rsi_os  = [25, 30, 35]
        rsi_ob  = [65, 70]
        vol_th  = [1.0, 1.2, 1.5]
        sl      = [0.03, 0.05]
        tp      = [0.08, 0.12, 0.18]
        pos     = [0.30]
    elif label == "1h":
        fast    = [5, 8, 10]
        slow    = [20, 30, 50]
        rsi_w   = [14]
        rsi_os  = [25, 30, 35]
        rsi_ob  = [65, 70, 75]
        vol_th  = [1.0, 1.3, 1.6]
        sl      = [0.03, 0.05]
        tp      = [0.10, 0.15, 0.20]
        pos     = [0.30]
    elif label == "4h":
        fast    = [5, 8, 10]
        slow    = [20, 30, 50]
        rsi_w   = [14]
        rsi_os  = [25, 30, 35]
        rsi_ob  = [65, 70, 75]
        vol_th  = [1.0, 1.3, 1.6]
        sl      = [0.04, 0.06]
        tp      = [0.12, 0.18, 0.25]
        pos     = [0.30]
    else:  # 1d
        fast    = [5, 10, 20]
        slow    = [20, 30, 50, 60]
        rsi_w   = [14]
        rsi_os  = [25, 30, 35]
        rsi_ob  = [65, 70, 75]
        vol_th  = [1.0, 1.2, 1.5]
        sl      = [0.05, 0.08]
        tp      = [0.15, 0.25]
        pos     = [0.30]

    grid: List[Dict[str, Any]] = []
    for f, s, rw, ros, rob, v, st, t, p in product(
        fast, slow, rsi_w, rsi_os, rsi_ob, vol_th, sl, tp, pos
    ):
        if f >= s:
            continue  # fast must be strictly faster than slow
        if ros >= rob:
            continue
        grid.append({
            "fast_window": f,
            "slow_window": s,
            "rsi_window": rw,
            "rsi_oversold": float(ros),
            "rsi_overbought": float(rob),
            "volume_ratio_threshold": float(v),
            "stop_loss_pct": float(st),
            "take_profit_pct": float(t),
            "position_pct": float(p),
            # LIVE_SUBMIT must be True for the strategy's place_limit() to fire.
            # In backtest this only routes through CtaTemplate.buy/sell, never
            # to a live broker. The DSL alert() path is harmless.
            "LIVE_SUBMIT": True,
        })
    return grid


# ---------------------------------------------------------------------------
# Per-interval orchestration.
# ---------------------------------------------------------------------------
def optimize_interval(
    *,
    label: str,
    spec: IntervalSpec,
    strategy_path: Path,
    vt_symbol: str,
    is_start: datetime,
    is_end: datetime,
    oos_start: datetime,
    oos_end: datetime,
    capital: float,
    out_dir: Path,
    min_trades: int,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(f"opt.{label}")
    log.info("=" * 70)
    log.info("Interval %s | base=%s window=%d", label, spec.base_interval.value, spec.window)

    # 1. Load base bars covering both IS and OOS in one shot.
    symbol, exchange_str = vt_symbol.split(".")
    exchange = Exchange(exchange_str)
    full_start = min(is_start, oos_start)
    full_end = max(is_end, oos_end)
    base_bars = load_bars(symbol, exchange, spec.base_interval, full_start, full_end)
    log.info("loaded %d base bars over [%s, %s]", len(base_bars), full_start.date(), full_end.date())
    if not base_bars:
        log.error("no base bars; skipping interval")
        return {"label": label, "status": "no_data"}

    # 2. Resample once for the whole window.
    bars = resample_bars(base_bars, spec.window, label)

    # 3. Build the CTA class (futu DSL adapter).
    cta_cls = make_cta_class(
        strategy_path=strategy_path,
        runtime_interval=Interval.MINUTE,  # unused inside adapter beyond aliasing
        class_name=f"FutuDsl_{strategy_path.stem}_{label}",
        initial_capital=capital,
    )

    # 4. IS grid search.
    grid = build_param_grid(label)
    log.info("grid size = %d", len(grid))

    grid_results: List[Dict[str, Any]] = []
    t0 = time.time()
    for i, setting in enumerate(grid, 1):
        try:
            stats = run_one_backtest(
                cta_class=cta_cls,
                setting=setting,
                vt_symbol=vt_symbol,
                bars=bars,
                start=is_start,
                end=is_end,
                capital=capital,
                annual_days=spec.annual_days,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("grid #%d setting=%s error=%s", i, setting, exc)
            continue
        row = {
            "params": setting,
            "trade_count": int(stats.get("total_trade_count", 0) or 0),
            "sharpe": float(stats.get("sharpe_ratio", 0.0) or 0.0),
            "total_return": float(stats.get("total_return", 0.0) or 0.0),
            "annual_return": float(stats.get("annual_return", 0.0) or 0.0),
            "max_drawdown_pct": float(stats.get("max_ddpercent", 0.0) or 0.0),
            "win_rate": float(stats.get("win_loss_pct_ratio", 0.0) or 0.0),
            "_bars": stats.get("_bars", 0),
        }
        grid_results.append(row)
        if i % 16 == 0 or i == len(grid):
            elapsed = time.time() - t0
            log.info("  progress %d/%d  elapsed=%.1fs", i, len(grid), elapsed)

    # 5. Filter & rank.
    eligible = [r for r in grid_results if r["trade_count"] >= min_trades]
    if not eligible:
        log.warning("no params produced >=%d trades; falling back to any with trade>0", min_trades)
        eligible = [r for r in grid_results if r["trade_count"] > 0] or grid_results

    eligible.sort(key=lambda r: (r["sharpe"], r["annual_return"]), reverse=True)
    best = eligible[0] if eligible else None

    # 6. OOS validation with best params.
    oos_stats: Dict[str, Any] = {}
    if best:
        log.info("best IS params=%s sharpe=%.3f trades=%d", best["params"], best["sharpe"], best["trade_count"])
        try:
            os_raw = run_one_backtest(
                cta_class=cta_cls,
                setting=best["params"],
                vt_symbol=vt_symbol,
                bars=bars,
                start=oos_start,
                end=oos_end,
                capital=capital,
                annual_days=spec.annual_days,
            )
            oos_stats = {
                "trade_count": int(os_raw.get("total_trade_count", 0) or 0),
                "sharpe": float(os_raw.get("sharpe_ratio", 0.0) or 0.0),
                "total_return": float(os_raw.get("total_return", 0.0) or 0.0),
                "annual_return": float(os_raw.get("annual_return", 0.0) or 0.0),
                "max_drawdown_pct": float(os_raw.get("max_ddpercent", 0.0) or 0.0),
                "win_rate": float(os_raw.get("win_loss_pct_ratio", 0.0) or 0.0),
                "_bars": os_raw.get("_bars", 0),
            }
            log.info("OOS sharpe=%.3f trades=%d total_return=%.2f%%",
                     oos_stats["sharpe"], oos_stats["trade_count"], oos_stats["total_return"])
        except Exception as exc:  # noqa: BLE001
            log.error("OOS run failed: %s", exc)
            oos_stats = {"_error": str(exc)}

    # 7. Persist.
    summary = {
        "label": label,
        "vt_symbol": vt_symbol,
        "base_interval": spec.base_interval.value,
        "resample_window": spec.window,
        "is_window": [is_start.isoformat(), is_end.isoformat()],
        "oos_window": [oos_start.isoformat(), oos_end.isoformat()],
        "grid_size": len(grid),
        "evaluated": len(grid_results),
        "eligible_count": len(eligible),
        "min_trades_filter": min_trades,
        "best_is": best,
        "oos": oos_stats,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # full grid CSV
    import csv
    csv_path = out_dir / "grid_results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow([
            "fast", "slow", "rsi_w", "rsi_os", "rsi_ob",
            "vol_th", "sl", "tp", "pos",
            "trades", "sharpe", "total_return", "annual_return",
            "max_dd_pct", "win_rate", "bars",
        ])
        for r in grid_results:
            p = r["params"]
            writer.writerow([
                p["fast_window"], p["slow_window"], p["rsi_window"],
                p["rsi_oversold"], p["rsi_overbought"],
                p["volume_ratio_threshold"], p["stop_loss_pct"],
                p["take_profit_pct"], p["position_pct"],
                r["trade_count"], f"{r['sharpe']:.4f}",
                f"{r['total_return']:.4f}", f"{r['annual_return']:.4f}",
                f"{r['max_drawdown_pct']:.4f}", f"{r['win_rate']:.4f}",
                r["_bars"],
            ])
    log.info("wrote %s and %s", out_dir / "summary.json", csv_path)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-interval IS+OOS optimizer.")
    p.add_argument("--strategy", default="tmp/strategy/strategy_simple_multifactor.py")
    p.add_argument("--symbol", default="NVDA.SMART", help="vt_symbol")
    p.add_argument("--intervals", default="5m,10m,30m,1h,4h,1d")
    p.add_argument("--is-start", default="2024-01-01")
    p.add_argument("--is-end", default="2025-11-12")
    p.add_argument("--oos-start", default="2025-11-13")
    p.add_argument("--oos-end", default="2026-05-13")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--min-trades", type=int, default=5,
                   help="IS filter: minimum total_trade_count to be eligible")
    p.add_argument("--out-root", default="tmp/data/optimize_results")
    p.add_argument("--tag", default="", help="extra suffix for output dir")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    )

    strategy_path = (Path(args.strategy)
                     if Path(args.strategy).is_absolute()
                     else _PROJECT_ROOT / args.strategy)
    if not strategy_path.exists():
        logger.error("strategy not found: %s", strategy_path)
        return 2

    is_start = datetime.fromisoformat(args.is_start)
    is_end = datetime.fromisoformat(args.is_end).replace(hour=23, minute=59, second=59)
    oos_start = datetime.fromisoformat(args.oos_start)
    oos_end = datetime.fromisoformat(args.oos_end).replace(hour=23, minute=59, second=59)

    intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]
    for lab in intervals:
        if lab not in INTERVAL_SPECS:
            logger.error("unsupported interval %r; allowed: %s",
                         lab, list(INTERVAL_SPECS))
            return 2

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sym_clean = args.symbol.replace(".", "_")
    tag = f"_{args.tag}" if args.tag else ""
    out_root = (Path(args.out_root) if Path(args.out_root).is_absolute()
                else _PROJECT_ROOT / args.out_root) / f"{strategy_path.stem}_{sym_clean}_{ts}{tag}"
    out_root.mkdir(parents=True, exist_ok=True)
    logger.info("output dir: %s", out_root)

    run_meta = {
        "strategy": str(strategy_path),
        "vt_symbol": args.symbol,
        "intervals": intervals,
        "is_window": [args.is_start, args.is_end],
        "oos_window": [args.oos_start, args.oos_end],
        "capital": args.capital,
        "min_trades": args.min_trades,
        "started_at": ts,
    }
    (out_root / "run_meta.json").write_text(
        json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summaries: List[Dict[str, Any]] = []
    t_start = time.time()
    for lab in intervals:
        spec = INTERVAL_SPECS[lab]
        try:
            s = optimize_interval(
                label=lab,
                spec=spec,
                strategy_path=strategy_path,
                vt_symbol=args.symbol,
                is_start=is_start,
                is_end=is_end,
                oos_start=oos_start,
                oos_end=oos_end,
                capital=args.capital,
                out_dir=out_root / lab,
                min_trades=args.min_trades,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("interval %s failed: %s", lab, exc)
            s = {"label": lab, "status": "error", "error": str(exc)}
        summaries.append(s)

    # Cross-interval summary table.
    rows = []
    for s in summaries:
        if not isinstance(s, dict) or s.get("status") in ("no_data", "error"):
            rows.append({"label": s.get("label", "?"), "status": s.get("status", "?")})
            continue
        best = s.get("best_is") or {}
        oos = s.get("oos") or {}
        rows.append({
            "label": s["label"],
            "is_sharpe": best.get("sharpe"),
            "is_trades": best.get("trade_count"),
            "is_total_return_pct": best.get("total_return"),
            "is_max_dd_pct": best.get("max_drawdown_pct"),
            "oos_sharpe": oos.get("sharpe"),
            "oos_trades": oos.get("trade_count"),
            "oos_total_return_pct": oos.get("total_return"),
            "oos_max_dd_pct": oos.get("max_drawdown_pct"),
            "best_params": best.get("params"),
        })

    (out_root / "cross_interval_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Markdown report
    md_lines = [
        f"# Optimization Report — {strategy_path.stem} on {args.symbol}",
        "",
        f"- IS window: `{args.is_start}` → `{args.is_end}`",
        f"- OOS window: `{args.oos_start}` → `{args.oos_end}`",
        f"- Capital: `{args.capital}`",
        f"- Min trades filter: `{args.min_trades}`",
        f"- Output dir: `{out_root.relative_to(_PROJECT_ROOT)}`",
        f"- Total elapsed: `{round(time.time() - t_start, 1)}s`",
        "",
        "## Cross-interval summary",
        "",
        "| interval | IS sharpe | IS trades | IS ret% | IS dd% | OOS sharpe | OOS trades | OOS ret% | OOS dd% |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "status" in r and r["status"] in ("no_data", "error"):
            md_lines.append(f"| {r['label']} | — | — | — | — | — | — | — | _{r['status']}_ |")
            continue
        def fmt(v, p=2):
            if v is None: return "—"
            return f"{v:.{p}f}" if isinstance(v, (int, float)) else str(v)
        md_lines.append(
            f"| {r['label']} | {fmt(r['is_sharpe'],3)} | {fmt(r['is_trades'],0)} | "
            f"{fmt(r['is_total_return_pct'],2)} | {fmt(r['is_max_dd_pct'],2)} | "
            f"{fmt(r['oos_sharpe'],3)} | {fmt(r['oos_trades'],0)} | "
            f"{fmt(r['oos_total_return_pct'],2)} | {fmt(r['oos_max_dd_pct'],2)} |"
        )
    md_lines.append("")
    md_lines.append("## Best params per interval (from IS)")
    md_lines.append("")
    for r in rows:
        if r.get("best_params"):
            md_lines.append(f"### {r['label']}")
            md_lines.append("")
            md_lines.append("```json")
            md_lines.append(json.dumps(r["best_params"], ensure_ascii=False, indent=2))
            md_lines.append("```")
            md_lines.append("")

    (out_root / "report.md").write_text("\n".join(md_lines), encoding="utf-8")
    logger.info("FINISHED. Report: %s", out_root / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
