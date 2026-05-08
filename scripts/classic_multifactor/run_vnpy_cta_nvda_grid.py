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


SYMBOL = "NVDA.US"
INTERVAL = "1m"
CAPITAL = 5000.0
RATE = 0.0003
SLIPPAGE = 0.05
SIZE = 1
PRICETICK = 0.01

OUTPUT_DIR = REPO_ROOT / "state" / "runs" / "classic_multifactor"

# Walk-forward windows (end dates inclusive, length ~ 1 month each)
WINDOWS: dict[str, dict[str, str]] = {
    "W1": {"start": "2026-04-08", "end": "2026-05-07"},
    "W2": {"start": "2026-03-08", "end": "2026-04-07"},
    "W3": {"start": "2026-02-08", "end": "2026-03-07"},
}


BASE_SETTING: dict[str, Any] = {
    "fast_window": 6,
    "slow_window": 24,
    "momentum_window": 12,
    "atr_window": 14,
    "exit_score": 0.46,
    "max_order_value": 1250.0,
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
    # Fixed across grid
    "max_intraday_trades": 4,
}


# Refined grid around R3/R5 winners: signal_interval x entry_score
SIGNAL_INTERVALS = [8, 10, 12, 15]
ENTRY_SCORES = [0.66, 0.68, 0.70, 0.72]


def build_grid() -> list[dict[str, Any]]:
    grid: list[dict[str, Any]] = []
    idx = 1
    for si in SIGNAL_INTERVALS:
        for es in ENTRY_SCORES:
            grid.append(
                {
                    "tag": f"G{idx:02d}",
                    "signal_interval_minutes": si,
                    "entry_score": es,
                    "max_intraday_trades": 4,
                    "intent": f"si={si}_es={es}",
                }
            )
            idx += 1
    return grid


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


def _compute_trade_stats(engine: Any) -> dict[str, Any]:
    """Reconstruct trade pairs (open+close) from engine.get_all_trades() and
    compute win_rate / avg_win / avg_loss / profit_loss_ratio / avg_holding_minutes.

    vn.py BacktestingEngine trades only contain direction/offset/price/volume;
    we pair OPEN offset with subsequent CLOSE/CLOSETODAY/CLOSEYESTERDAY offsets
    using a FIFO position queue. Multi-contract aggregation is handled by
    flattening to unit lots.
    """
    try:
        trades = list(engine.get_all_trades())
    except Exception:
        return {"pairs": 0}

    if not trades:
        return {"pairs": 0}

    # Sort by datetime (engine should already keep order but be safe)
    try:
        trades.sort(key=lambda t: getattr(t, "datetime", None) or 0)
    except Exception:
        pass

    open_queue: list[dict[str, Any]] = []  # each entry: {direction, price, dt}
    pairs: list[dict[str, Any]] = []

    for t in trades:
        direction = str(getattr(t, "direction", "")).split(".")[-1].lower()  # long/short
        offset = str(getattr(t, "offset", "")).split(".")[-1].lower()  # open/close/...
        price = float(getattr(t, "price", 0.0) or 0.0)
        volume = int(float(getattr(t, "volume", 0) or 0))
        dt = getattr(t, "datetime", None)

        if offset == "open":
            for _ in range(volume):
                open_queue.append({"direction": direction, "price": price, "dt": dt})
            continue

        # close-like offsets
        for _ in range(volume):
            if not open_queue:
                break
            op = open_queue.pop(0)
            # long open -> pnl = close - open; short open -> pnl = open - close
            if op["direction"] == "long":
                pnl = price - op["price"]
            else:
                pnl = op["price"] - price
            hold_minutes: float | None = None
            try:
                if op["dt"] is not None and dt is not None:
                    hold_minutes = (dt - op["dt"]).total_seconds() / 60.0
            except Exception:
                hold_minutes = None
            pairs.append(
                {
                    "direction": op["direction"],
                    "open_price": op["price"],
                    "close_price": price,
                    "pnl_per_unit": pnl,
                    "hold_minutes": hold_minutes,
                }
            )

    if not pairs:
        return {"pairs": 0}

    wins = [p for p in pairs if p["pnl_per_unit"] > 0]
    losses = [p for p in pairs if p["pnl_per_unit"] < 0]
    flats = [p for p in pairs if p["pnl_per_unit"] == 0]

    def _avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 6) if xs else None

    win_pnls = [p["pnl_per_unit"] for p in wins]
    loss_pnls = [abs(p["pnl_per_unit"]) for p in losses]
    hold_minutes = [p["hold_minutes"] for p in pairs if p.get("hold_minutes") is not None]

    avg_win = _avg(win_pnls)
    avg_loss = _avg(loss_pnls)
    pl_ratio = None
    if avg_win is not None and avg_loss is not None and avg_loss > 0:
        pl_ratio = round(avg_win / avg_loss, 4)

    return {
        "pairs": len(pairs),
        "wins": len(wins),
        "losses": len(losses),
        "flats": len(flats),
        "win_rate_pair": round(len(wins) / len(pairs), 4) if pairs else None,
        "avg_win_pnl_per_unit": avg_win,
        "avg_loss_pnl_per_unit": avg_loss,
        "profit_loss_ratio_pair": pl_ratio,
        "avg_holding_minutes": _avg(hold_minutes) if hold_minutes else None,
        "open_positions_left": len(open_queue),
    }


def run_once(
    vt_symbol: str,
    cfg: dict[str, Any],
    window: dict[str, str],
    window_tag: str,
) -> dict[str, Any]:
    setting = dict(BASE_SETTING)
    setting["signal_interval_minutes"] = cfg["signal_interval_minutes"]
    setting["entry_score"] = cfg["entry_score"]
    setting["max_intraday_trades"] = cfg["max_intraday_trades"]

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
        "tag": cfg["tag"],
        "intent": cfg["intent"],
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

    out = OUTPUT_DIR / f"vnpy_cta_nvda_grid_{window_tag}_{cfg['tag']}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    digest = {k: safe_get(stats, k) for k in KEY_METRICS}
    return {
        "window_tag": window_tag,
        "tag": cfg["tag"],
        "signal_interval_minutes": cfg["signal_interval_minutes"],
        "entry_score": cfg["entry_score"],
        "max_intraday_trades": cfg["max_intraday_trades"],
        "status": stats.get("status"),
        "elapsed_sec": elapsed,
        "report_file": str(out.relative_to(REPO_ROOT)),
        **digest,
        "trade_pairs": trade_stats.get("pairs"),
        "win_rate_pair": trade_stats.get("win_rate_pair"),
        "profit_loss_ratio_pair": trade_stats.get("profit_loss_ratio_pair"),
        "avg_holding_minutes": trade_stats.get("avg_holding_minutes"),
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, int):
        return str(v)
    try:
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def build_markdown(rows_sorted: list[dict[str, Any]]) -> str:
    header = (
        "| tag | sig_int | entry | total_return | max_dd% | sharpe | rdd_ratio | "
        "trades | pairs | win_rate_p | pl_ratio_p | hold_min |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for r in rows_sorted:
        lines.append(
            "| {tag} | {si} | {es} | {tr} | {md} | {sh} | {rdd} | {tc} | {pp} | {wr} | {pl} | {hm} |".format(
                tag=r["tag"],
                si=r["signal_interval_minutes"],
                es=r["entry_score"],
                tr=_fmt(r.get("total_return")),
                md=_fmt(r.get("max_ddpercent")),
                sh=_fmt(r.get("sharpe_ratio")),
                rdd=_fmt(r.get("return_drawdown_ratio")),
                tc=r.get("total_trade_count") or r.get("trade_count_runtime") or 0,
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
    # fetch_futu_history=False: W2/W3 data must be pre-loaded into DB separately
    bars_meta = VnpyBarRepository(fetch_futu_history=False).load_us_bars(
        SYMBOL, start, end, INTERVAL
    )
    bar_count = len(bars_meta[2]) if bars_meta and len(bars_meta) >= 3 else 0
    print(f"[preload] bars loaded: count={bar_count} vt_symbol={vt_symbol}")

    grid = build_grid()
    rows: list[dict[str, Any]] = []
    for cfg in grid:
        print(
            f"[run  ] {window_tag} {cfg['tag']} si={cfg['signal_interval_minutes']} "
            f"es={cfg['entry_score']} cap={cfg['max_intraday_trades']} ..."
        )
        row = run_once(vt_symbol, cfg, window, window_tag)
        rows.append(row)
        print(
            f"[done ] {window_tag} {row['tag']} status={row['status']} "
            f"total_return={row.get('total_return')} rdd={row.get('return_drawdown_ratio')} "
            f"trades={row.get('total_trade_count')} pairs={row.get('trade_pairs')} "
            f"elapsed={row['elapsed_sec']}s"
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
        "window_tag": window_tag,
        "window": window,
        "capital": CAPITAL,
        "rank_key": "return_drawdown_ratio",
        "base_setting": BASE_SETTING,
        "grid_size": len(grid),
        "bar_count": bar_count,
        "rows_raw": rows,
        "rows_ranked": rows_sorted,
    }
    summary_path = OUTPUT_DIR / f"vnpy_cta_nvda_grid_{window_tag}_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    print(f"\n=== Ranking [{window_tag}] (by return_drawdown_ratio desc) ===")
    print(build_markdown(rows_sorted))
    print(f"\n[summary] {summary_path}")

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NVDA 1m refined grid (16 combos) for a given window.")
    parser.add_argument("--window", choices=list(WINDOWS.keys()), default="W1")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_window(args.window)


if __name__ == "__main__":
    main()
