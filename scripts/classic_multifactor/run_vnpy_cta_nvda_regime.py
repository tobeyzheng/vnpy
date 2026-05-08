"""NVDA 1m regime filter quick comparison.

Fixes the walk-forward champion parameters (G09: signal_interval=12,
entry_score=0.66) and compares market regime filter modes over a single
window (default W2 = the reversal month that the unfiltered strategy
struggled on). Four modes are tested: off / trend / vol / both.

Outputs a summary JSON and prints a markdown table.

Usage::

    python3 scripts/classic_multifactor/run_vnpy_cta_nvda_regime.py --window W2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.classic_multifactor.cta_backtest import ClassicCtaBacktestRunner
from scripts.classic_multifactor.data import VnpyBarRepository, parse_us_symbol
from scripts.classic_multifactor.run_vnpy_cta_nvda_grid import (
    BASE_SETTING,
    CAPITAL,
    INTERVAL,
    OUTPUT_DIR,
    PRICETICK,
    RATE,
    SIZE,
    SLIPPAGE,
    SYMBOL,
    WINDOWS,
    _compute_trade_stats,
    safe_get,
)


# G09 champion (walk-forward rank 1)
G09_SIGNAL_INTERVAL = 12
G09_ENTRY_SCORE = 0.66
G09_MAX_INTRADAY_TRADES = 4

# Regime filter presets to compare
REGIME_MODES: list[dict[str, Any]] = [
    {"tag": "R_off", "mode": "off", "intent": "baseline_no_filter"},
    {"tag": "R_trend", "mode": "trend", "intent": "trend_gate_only"},
    {"tag": "R_vol", "mode": "vol", "intent": "volatility_gate_only"},
    {"tag": "R_both", "mode": "both", "intent": "trend_and_volatility"},
]

# Regime filter defaults (daily-level)
REGIME_DEFAULTS: dict[str, Any] = {
    "regime_trend_lookback": 5,
    "regime_ema_span": 20,
    "regime_atr_pct_lo": 0.008,
    "regime_atr_pct_hi": 0.05,
    "regime_atr_days": 5,
}

KEY_METRICS = [
    "total_return",
    "annual_return",
    "max_ddpercent",
    "sharpe_ratio",
    "return_drawdown_ratio",
    "total_net_pnl",
    "total_trade_count",
    "win_rate",
    "profit_loss_ratio",
]


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, int):
        return str(v)
    try:
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def run_one(
    vt_symbol: str,
    window: dict[str, str],
    window_tag: str,
    preset: dict[str, Any],
) -> dict[str, Any]:
    setting = dict(BASE_SETTING)
    setting["signal_interval_minutes"] = G09_SIGNAL_INTERVAL
    setting["entry_score"] = G09_ENTRY_SCORE
    setting["max_intraday_trades"] = G09_MAX_INTRADAY_TRADES
    setting["regime_filter_mode"] = preset["mode"]
    setting.update(REGIME_DEFAULTS)

    start = datetime.fromisoformat(window["start"])
    end = datetime.fromisoformat(window["end"])

    runner = ClassicCtaBacktestRunner()
    t0 = time.time()
    stats, engine = runner.run(
        vt_symbol=vt_symbol,
        interval=INTERVAL,
        start=start,
        end=end,
        capital=CAPITAL,
        rate=RATE,
        slippage=SLIPPAGE,
        size=SIZE,
        pricetick=PRICETICK,
        setting=setting,
    )
    elapsed = round(time.time() - t0, 2)
    trade_stats = _compute_trade_stats(engine) if stats.get("status") == "ok" else {}

    report = {
        "window_tag": window_tag,
        "tag": preset["tag"],
        "intent": preset["intent"],
        "regime_mode": preset["mode"],
        "symbol": SYMBOL,
        "vt_symbol": vt_symbol,
        "interval": INTERVAL,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "elapsed_sec": elapsed,
        "setting": setting,
        "stats": stats,
        "trade_stats": trade_stats,
    }
    out = OUTPUT_DIR / f"vnpy_cta_nvda_regime_{window_tag}_{preset['tag']}.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    digest = {k: safe_get(stats, k) for k in KEY_METRICS}
    return {
        "window_tag": window_tag,
        "tag": preset["tag"],
        "regime_mode": preset["mode"],
        "intent": preset["intent"],
        "status": stats.get("status"),
        "elapsed_sec": elapsed,
        "report_file": str(out.relative_to(REPO_ROOT)),
        **digest,
        "trade_pairs": trade_stats.get("pairs"),
        "win_rate_pair": trade_stats.get("win_rate_pair"),
        "profit_loss_ratio_pair": trade_stats.get("profit_loss_ratio_pair"),
        "avg_holding_minutes": trade_stats.get("avg_holding_minutes"),
    }


def build_markdown(rows: list[dict[str, Any]]) -> str:
    header = (
        "| tag | regime | total_return | max_dd% | sharpe | rdd_ratio | "
        "trades | pairs | win_rate_p | pl_ratio_p | hold_min |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for r in rows:
        lines.append(
            "| {tag} | {rm} | {tr} | {md} | {sh} | {rdd} | {tc} | {pp} | {wr} | {pl} | {hm} |".format(
                tag=r["tag"],
                rm=r["regime_mode"],
                tr=_fmt(r.get("total_return")),
                md=_fmt(r.get("max_ddpercent")),
                sh=_fmt(r.get("sharpe_ratio")),
                rdd=_fmt(r.get("return_drawdown_ratio")),
                tc=r.get("total_trade_count") or 0,
                pp=r.get("trade_pairs") or 0,
                wr=_fmt(r.get("win_rate_pair")),
                pl=_fmt(r.get("profit_loss_ratio_pair")),
                hm=_fmt(r.get("avg_holding_minutes")),
            )
        )
    return "\n".join(lines)


def run_window(window_tag: str) -> dict[str, Any]:
    if window_tag not in WINDOWS:
        raise ValueError(f"unknown window tag: {window_tag}")
    window = WINDOWS[window_tag]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    _us, vt_symbol, _futu = parse_us_symbol(SYMBOL)
    start = datetime.fromisoformat(window["start"])
    end = datetime.fromisoformat(window["end"])

    print(
        f"[preload] loading bars {SYMBOL} {INTERVAL} {start.date()}~{end.date()} "
        f"(window={window_tag}) ... [no remote fetch]"
    )
    bars_meta = VnpyBarRepository(fetch_futu_history=False).load_us_bars(
        SYMBOL, start, end, INTERVAL
    )
    bar_count = len(bars_meta[2]) if bars_meta and len(bars_meta) >= 3 else 0
    print(f"[preload] bars loaded: count={bar_count} vt_symbol={vt_symbol}")

    rows: list[dict[str, Any]] = []
    for preset in REGIME_MODES:
        print(
            f"[run  ] {window_tag} {preset['tag']} mode={preset['mode']} ..."
        )
        row = run_one(vt_symbol, window, window_tag, preset)
        rows.append(row)
        print(
            f"[done ] {window_tag} {row['tag']} status={row['status']} "
            f"total_return={row.get('total_return')} rdd={row.get('return_drawdown_ratio')} "
            f"trades={row.get('total_trade_count')} pairs={row.get('trade_pairs')} "
            f"elapsed={row['elapsed_sec']}s"
        )

    summary = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "window_tag": window_tag,
        "window": window,
        "capital": CAPITAL,
        "fixed_params": {
            "signal_interval_minutes": G09_SIGNAL_INTERVAL,
            "entry_score": G09_ENTRY_SCORE,
            "max_intraday_trades": G09_MAX_INTRADAY_TRADES,
        },
        "regime_defaults": REGIME_DEFAULTS,
        "bar_count": bar_count,
        "rows": rows,
    }
    summary_path = OUTPUT_DIR / f"vnpy_cta_nvda_regime_{window_tag}_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print(f"\n=== Regime comparison [{window_tag}] (G09 fixed) ===")
    print(build_markdown(rows))
    print(f"\n[summary] {summary_path}")

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NVDA 1m regime-filter quick comparison (G09 fixed, 4 modes).",
    )
    parser.add_argument("--window", choices=list(WINDOWS.keys()), default="W2")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_window(args.window)


if __name__ == "__main__":
    main()
