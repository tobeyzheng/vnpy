from __future__ import annotations

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


SYMBOL = "NVDA.US"
INTERVAL = "1m"
START = datetime.fromisoformat("2026-04-08")
END = datetime.fromisoformat("2026-05-07")
CAPITAL = 20000.0
RATE = 0.0003
SLIPPAGE = 0.05
SIZE = 1
PRICETICK = 0.01

OUTPUT_DIR = REPO_ROOT / "state" / "runs" / "classic_multifactor"
SUMMARY_PATH = OUTPUT_DIR / "vnpy_cta_nvda_tuned_summary.json"


BASE_SETTING: dict[str, Any] = {
    "fast_window": 6,
    "slow_window": 24,
    "momentum_window": 12,
    "atr_window": 14,
    "exit_score": 0.46,
    "max_order_value": 5000.0,
    "capital": CAPITAL,
    "confirm_bars": 1,
    "min_volume_ratio": 0.8,
    "min_atr_pct": 0.0012,
    "min_trend_score": 0.55,
    "stop_atr": 1.5,
    "take_profit_atr": 2.5,
    "trailing_atr": 2.0,
    "entry_cooldown_minutes": 30,
    "min_hold_minutes": 20,
    "no_new_entry_after": "15:30",
}


GRID: list[dict[str, Any]] = [
    {"tag": "R1", "signal_interval_minutes": 5, "entry_score": 0.66, "max_intraday_trades": 4, "intent": "baseline"},
    {"tag": "R2", "signal_interval_minutes": 3, "entry_score": 0.66, "max_intraday_trades": 4, "intent": "faster_signal"},
    {"tag": "R3", "signal_interval_minutes": 10, "entry_score": 0.66, "max_intraday_trades": 4, "intent": "slower_signal"},
    {"tag": "R4", "signal_interval_minutes": 5, "entry_score": 0.64, "max_intraday_trades": 4, "intent": "threshold_loose"},
    {"tag": "R5", "signal_interval_minutes": 5, "entry_score": 0.68, "max_intraday_trades": 4, "intent": "threshold_tight"},
    {"tag": "R6", "signal_interval_minutes": 5, "entry_score": 0.66, "max_intraday_trades": 2, "intent": "cap_tight"},
    {"tag": "R7", "signal_interval_minutes": 5, "entry_score": 0.66, "max_intraday_trades": 6, "intent": "cap_loose"},
]


KEY_METRICS = [
    "total_return",
    "annual_return",
    "max_ddpercent",
    "sharpe_ratio",
    "return_drawdown_ratio",
    "total_net_pnl",
    "total_commission",
    "total_slippage",
    "total_trade_count",
    "win_rate",
    "profit_loss_ratio",
    "trade_count_runtime",
]


def safe_get(stats: dict[str, Any], key: str) -> Any:
    v = stats.get(key)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
    return v


def run_once(vt_symbol: str, cfg: dict[str, Any]) -> dict[str, Any]:
    setting = dict(BASE_SETTING)
    setting["signal_interval_minutes"] = cfg["signal_interval_minutes"]
    setting["entry_score"] = cfg["entry_score"]
    setting["max_intraday_trades"] = cfg["max_intraday_trades"]

    runner = ClassicCtaBacktestRunner()
    t0 = time.time()
    stats, _engine = runner.run(
        vt_symbol=vt_symbol,
        interval=INTERVAL,
        start=START,
        end=END,
        capital=CAPITAL,
        rate=RATE,
        slippage=SLIPPAGE,
        size=SIZE,
        pricetick=PRICETICK,
        setting=setting,
    )
    elapsed = round(time.time() - t0, 2)

    report = {
        "tag": cfg["tag"],
        "intent": cfg["intent"],
        "symbol": SYMBOL,
        "vt_symbol": vt_symbol,
        "interval": INTERVAL,
        "start": START.isoformat(),
        "end": END.isoformat(),
        "elapsed_sec": elapsed,
        "setting": setting,
        "stats": stats,
    }

    out = OUTPUT_DIR / f"vnpy_cta_nvda_tuned_{cfg['tag']}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    digest = {k: safe_get(stats, k) for k in KEY_METRICS}
    return {
        "tag": cfg["tag"],
        "intent": cfg["intent"],
        "signal_interval_minutes": cfg["signal_interval_minutes"],
        "entry_score": cfg["entry_score"],
        "max_intraday_trades": cfg["max_intraday_trades"],
        "status": stats.get("status"),
        "elapsed_sec": elapsed,
        "report_file": str(out.relative_to(REPO_ROOT)),
        **digest,
    }


def build_markdown(rows_sorted: list[dict[str, Any]]) -> str:
    header = "| tag | sig_int | entry | cap | total_return | max_dd% | sharpe | rdd_ratio | trades | win_rate | pl_ratio |"
    sep = "|---|---|---|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for r in rows_sorted:
        lines.append(
            "| {tag} | {si} | {es} | {cap} | {tr} | {md} | {sh} | {rdd} | {tc} | {wr} | {pl} |".format(
                tag=r["tag"],
                si=r["signal_interval_minutes"],
                es=r["entry_score"],
                cap=r["max_intraday_trades"],
                tr=_fmt(r.get("total_return")),
                md=_fmt(r.get("max_ddpercent")),
                sh=_fmt(r.get("sharpe_ratio")),
                rdd=_fmt(r.get("return_drawdown_ratio")),
                tc=r.get("total_trade_count") or r.get("trade_count_runtime") or 0,
                wr=_fmt(r.get("win_rate")),
                pl=_fmt(r.get("profit_loss_ratio")),
            )
        )
    return "\n".join(lines)


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, (int,)):
        return str(v)
    try:
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _us_symbol, vt_symbol, _futu_code = parse_us_symbol(SYMBOL)

    print(f"[preload] loading bars {SYMBOL} {INTERVAL} {START.date()}~{END.date()} ...")
    bars_meta = VnpyBarRepository(fetch_futu_history=True).load_us_bars(SYMBOL, START, END, INTERVAL)
    bar_count = len(bars_meta[2]) if bars_meta and len(bars_meta) >= 3 else 0
    print(f"[preload] bars loaded: count={bar_count} vt_symbol={vt_symbol}")

    rows: list[dict[str, Any]] = []
    for cfg in GRID:
        print(f"[run ] {cfg['tag']} si={cfg['signal_interval_minutes']} es={cfg['entry_score']} cap={cfg['max_intraday_trades']} ({cfg['intent']}) ...")
        row = run_once(vt_symbol, cfg)
        rows.append(row)
        print(
            f"[done] {row['tag']} status={row['status']} total_return={row.get('total_return')} "
            f"max_dd%={row.get('max_ddpercent')} rdd={row.get('return_drawdown_ratio')} trades={row.get('total_trade_count')} elapsed={row['elapsed_sec']}s"
        )

    def _rank_key(r: dict[str, Any]) -> float:
        v = r.get("return_drawdown_ratio")
        try:
            f = float(v)
            if f != f:
                return float("-inf")
            return f
        except Exception:
            return float("-inf")

    rows_sorted = sorted(rows, key=_rank_key, reverse=True)

    summary = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "capital": CAPITAL,
        "rank_key": "return_drawdown_ratio",
        "base_setting": BASE_SETTING,
        "grid": GRID,
        "bar_count": bar_count,
        "rows_raw": rows,
        "rows_ranked": rows_sorted,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("\n=== Ranking (by return_drawdown_ratio desc) ===")
    print(build_markdown(rows_sorted))
    print(f"\n[summary] {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
