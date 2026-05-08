from __future__ import annotations

import argparse
import csv
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


SWEEP_DIMS = ("signal_interval_minutes", "entry_score", "max_intraday_trades")

STAT_FIELDS = (
    "sharpe_ratio",
    "ewm_sharpe",
    "total_return",
    "annual_return",
    "max_ddpercent",
    "return_drawdown_ratio",
    "total_trade_count",
    "end_balance",
)


def _parse_int_list(text: str) -> list[int]:
    return [int(item.strip()) for item in text.split(",") if item.strip()]


def _parse_float_list(text: str) -> list[float]:
    return [float(item.strip()) for item in text.split(",") if item.strip()]


def _parse_symbols(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classic multifactor vn.py CTA sweep (signal_interval x entry_score x max_intraday_trades)"
    )
    parser.add_argument("--symbols", default="NVDA.US", help="comma separated symbols, e.g. NVDA.US,TSLA.US")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--interval", choices=["1m"], default="1m", help="sweep only meaningful on 1m data")
    parser.add_argument("--capital", type=float, default=20000.0)
    parser.add_argument("--rate", type=float, default=0.0003)
    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument("--size", type=int, default=1)
    parser.add_argument("--pricetick", type=float, default=0.01)

    # sweep grids
    parser.add_argument("--signal-intervals", default="3,5,10")
    parser.add_argument("--entry-scores", default="0.62,0.64,0.66,0.68")
    parser.add_argument("--max-intraday-trades-list", default="2,4,6")

    # base strategy params (aligned with run.py --minute-profile defaults)
    parser.add_argument("--fast-window", type=int, default=6)
    parser.add_argument("--slow-window", type=int, default=24)
    parser.add_argument("--momentum-window", type=int, default=12)
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--exit-score", type=float, default=0.46)
    parser.add_argument("--max-order-value", type=float, default=5000.0)
    parser.add_argument("--confirm-bars", type=int, default=1)
    parser.add_argument("--min-volume-ratio", type=float, default=0.8)
    parser.add_argument("--min-atr-pct", type=float, default=0.0012)
    parser.add_argument("--min-trend-score", type=float, default=0.55)
    parser.add_argument("--stop-atr", type=float, default=1.5)
    parser.add_argument("--take-profit-atr", type=float, default=2.5)
    parser.add_argument("--trailing-atr", type=float, default=2.0)
    parser.add_argument("--entry-cooldown-minutes", type=int, default=30)
    parser.add_argument("--min-hold-minutes", type=int, default=20)
    parser.add_argument("--no-new-entry-after", default="15:30")

    parser.add_argument(
        "--rank-key",
        default="return_drawdown_ratio",
        choices=["sharpe_ratio", "ewm_sharpe", "total_return", "return_drawdown_ratio"],
    )
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--output", default="")
    parser.add_argument("--csv", default="")
    parser.add_argument("--no-fetch-futu", action="store_true", help="disable Futu OpenD history fallback")
    return parser


def _build_setting(args: argparse.Namespace, *, signal_interval: int, entry_score: float, max_intraday_trades: int) -> dict[str, Any]:
    return {
        "fast_window": args.fast_window,
        "slow_window": args.slow_window,
        "momentum_window": args.momentum_window,
        "atr_window": args.atr_window,
        "entry_score": entry_score,
        "exit_score": args.exit_score,
        "max_order_value": args.max_order_value,
        "capital": args.capital,
        "signal_interval_minutes": signal_interval,
        "confirm_bars": args.confirm_bars,
        "min_volume_ratio": args.min_volume_ratio,
        "min_atr_pct": args.min_atr_pct,
        "min_trend_score": args.min_trend_score,
        "stop_atr": args.stop_atr,
        "take_profit_atr": args.take_profit_atr,
        "trailing_atr": args.trailing_atr,
        "max_intraday_trades": max_intraday_trades,
        "entry_cooldown_minutes": args.entry_cooldown_minutes,
        "min_hold_minutes": args.min_hold_minutes,
        "no_new_entry_after": args.no_new_entry_after,
    }


def _base_setting_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    setting = _build_setting(args, signal_interval=0, entry_score=0.0, max_intraday_trades=0)
    for key in SWEEP_DIMS:
        setting.pop(key, None)
    return setting


def _stat_number(stats: dict[str, Any], key: str) -> float:
    value = stats.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _stats_summary(stats: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"status": stats.get("status", "unknown")}
    for key in STAT_FIELDS:
        if key in stats:
            summary[key] = stats.get(key)
    if "trade_count_runtime" in stats:
        summary["trade_count_runtime"] = stats["trade_count_runtime"]
    return summary


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main() -> None:
    args = build_parser().parse_args()

    symbols = _parse_symbols(args.symbols)
    if not symbols:
        raise SystemExit("--symbols must contain at least one symbol")
    signal_intervals = _parse_int_list(args.signal_intervals)
    entry_scores = _parse_float_list(args.entry_scores)
    max_intraday_trades_list = _parse_int_list(args.max_intraday_trades_list)
    if not signal_intervals or not entry_scores or not max_intraday_trades_list:
        raise SystemExit("sweep grids must be non-empty")

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)

    repo = VnpyBarRepository(fetch_futu_history=not args.no_fetch_futu)

    runner = ClassicCtaBacktestRunner()

    rows: list[dict[str, Any]] = []
    total = len(symbols) * len(signal_intervals) * len(entry_scores) * len(max_intraday_trades_list)
    idx = 0

    for symbol in symbols:
        print(f"[sweep] loading bars for {symbol} {args.start}..{args.end} {args.interval}", flush=True)
        _us_symbol, vt_symbol, _futu_code = parse_us_symbol(symbol)
        repo.load_us_bars(symbol, start, end, args.interval)

        for si in signal_intervals:
            for es in entry_scores:
                for mit in max_intraday_trades_list:
                    idx += 1
                    setting = _build_setting(
                        args,
                        signal_interval=si,
                        entry_score=es,
                        max_intraday_trades=mit,
                    )
                    t0 = time.time()
                    status = "ok"
                    error_message = ""
                    stats: dict[str, Any] = {}
                    try:
                        stats, _engine = runner.run(
                            vt_symbol=vt_symbol,
                            interval=args.interval,
                            start=start,
                            end=end,
                            capital=args.capital,
                            rate=args.rate,
                            slippage=args.slippage,
                            size=args.size,
                            pricetick=args.pricetick,
                            setting=setting,
                        )
                    except Exception as exc:  # noqa: BLE001
                        status = "error"
                        error_message = f"{type(exc).__name__}: {exc}"
                        stats = {"status": "error"}
                    elapsed = round(time.time() - t0, 3)

                    summary = _stats_summary(stats)
                    rows.append(
                        {
                            "symbol": symbol,
                            "vt_symbol": vt_symbol,
                            "params": {
                                "signal_interval_minutes": si,
                                "entry_score": es,
                                "max_intraday_trades": mit,
                            },
                            "stats": stats,
                            "stats_summary": summary,
                            "status": status if status == "error" else stats.get("status", "ok"),
                            "error_message": error_message,
                            "elapsed_sec": elapsed,
                        }
                    )

                    sharpe = _stat_number(stats, "sharpe_ratio")
                    ret = _stat_number(stats, "total_return")
                    dd = _stat_number(stats, "max_ddpercent")
                    rdr = _stat_number(stats, "return_drawdown_ratio")
                    trades = stats.get("total_trade_count", 0)
                    print(
                        f"[{idx}/{total}] {symbol} si={si} es={es} mit={mit} "
                        f"-> status={rows[-1]['status']} sharpe={sharpe:.3f} ret={ret:.4f} "
                        f"dd={dd:.4f} rdr={rdr:.3f} trades={trades} {elapsed}s",
                        flush=True,
                    )

    # build ranking (primary: rank_key desc; tie-break: total_return desc, then sharpe_ratio desc)
    def _rank_tuple(row: dict[str, Any]) -> tuple[float, float, float]:
        if row["status"] != "ok":
            return (float("-inf"), float("-inf"), float("-inf"))
        return (
            _stat_number(row["stats"], args.rank_key),
            _stat_number(row["stats"], "total_return"),
            _stat_number(row["stats"], "sharpe_ratio"),
        )

    global_ranking: list[dict[str, Any]] = []
    for rank, row in enumerate(sorted(rows, key=_rank_tuple, reverse=True), start=1):
        global_ranking.append(
            {
                "rank": rank,
                "symbol": row["symbol"],
                "params": row["params"],
                "status": row["status"],
                "stats_summary": row["stats_summary"],
            }
        )

    per_symbol_best: list[dict[str, Any]] = []
    for symbol in symbols:
        sym_rows = [r for r in rows if r["symbol"] == symbol and r["status"] == "ok"]
        if not sym_rows:
            per_symbol_best.append({"symbol": symbol, "params": None, "stats_summary": None, "status": "no_ok_result"})
            continue
        best = max(sym_rows, key=lambda r: (
            _stat_number(r["stats"], args.rank_key),
            _stat_number(r["stats"], "total_return"),
            _stat_number(r["stats"], "sharpe_ratio"),
        ))
        per_symbol_best.append(
            {
                "symbol": symbol,
                "params": best["params"],
                "stats_summary": best["stats_summary"],
                "status": best["status"],
            }
        )

    report = {
        "strategy": "classic_multifactor_no_llm_vnpy_cta_sweep",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "interval": args.interval,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "symbols": symbols,
        "grid": {
            "signal_interval_minutes": signal_intervals,
            "entry_score": entry_scores,
            "max_intraday_trades": max_intraday_trades_list,
        },
        "base_setting": _base_setting_snapshot(args),
        "rank_key": args.rank_key,
        "per_symbol_best": per_symbol_best,
        "global_ranking": global_ranking,
        "rows": rows,
    }

    out = Path(args.output) if args.output else REPO_ROOT / "state" / "runs" / "classic_multifactor" / "vnpy_cta_sweep_report.json"
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    csv_path = Path(args.csv) if args.csv else REPO_ROOT / "state" / "runs" / "classic_multifactor" / "vnpy_cta_sweep_rows.csv"
    if not csv_path.is_absolute():
        csv_path = REPO_ROOT / csv_path
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_header = [
        "symbol",
        "signal_interval_minutes",
        "entry_score",
        "max_intraday_trades",
        "status",
        "sharpe_ratio",
        "ewm_sharpe",
        "total_return",
        "annual_return",
        "max_ddpercent",
        "return_drawdown_ratio",
        "total_trade_count",
        "end_balance",
        "elapsed_sec",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(csv_header)
        for row in rows:
            stats = row["stats"]
            writer.writerow(
                [
                    row["symbol"],
                    row["params"]["signal_interval_minutes"],
                    row["params"]["entry_score"],
                    row["params"]["max_intraday_trades"],
                    row["status"],
                    stats.get("sharpe_ratio", ""),
                    stats.get("ewm_sharpe", ""),
                    stats.get("total_return", ""),
                    stats.get("annual_return", ""),
                    stats.get("max_ddpercent", ""),
                    stats.get("return_drawdown_ratio", ""),
                    stats.get("total_trade_count", ""),
                    stats.get("end_balance", ""),
                    row["elapsed_sec"],
                ]
            )

    # stdout summary tables
    print("\n=== Per-Symbol Best (rank_key=%s) ===" % args.rank_key)
    best_rows = []
    for item in per_symbol_best:
        params = item["params"] or {}
        summary = item["stats_summary"] or {}
        best_rows.append(
            [
                item["symbol"],
                item["status"],
                _fmt(params.get("signal_interval_minutes", "-")),
                _fmt(params.get("entry_score", "-")),
                _fmt(params.get("max_intraday_trades", "-")),
                _fmt(summary.get("sharpe_ratio", "-")),
                _fmt(summary.get("total_return", "-")),
                _fmt(summary.get("max_ddpercent", "-")),
                _fmt(summary.get("return_drawdown_ratio", "-")),
                _fmt(summary.get("total_trade_count", "-")),
            ]
        )
    print(
        _md_table(
            [
                "symbol",
                "status",
                "signal_interval",
                "entry_score",
                "max_intraday_trades",
                "sharpe",
                "total_return",
                "max_ddpercent",
                "return_drawdown_ratio",
                "trades",
            ],
            best_rows,
        )
    )

    print("\n=== Top-%d Global Ranking (rank_key=%s) ===" % (args.top, args.rank_key))
    top_rows = []
    for entry in global_ranking[: max(args.top, 0)]:
        params = entry["params"]
        summary = entry["stats_summary"] or {}
        top_rows.append(
            [
                str(entry["rank"]),
                entry["symbol"],
                entry["status"],
                _fmt(params.get("signal_interval_minutes")),
                _fmt(params.get("entry_score")),
                _fmt(params.get("max_intraday_trades")),
                _fmt(summary.get("sharpe_ratio", "-")),
                _fmt(summary.get("total_return", "-")),
                _fmt(summary.get("max_ddpercent", "-")),
                _fmt(summary.get("return_drawdown_ratio", "-")),
                _fmt(summary.get("total_trade_count", "-")),
            ]
        )
    print(
        _md_table(
            [
                "rank",
                "symbol",
                "status",
                "signal_interval",
                "entry_score",
                "max_intraday_trades",
                "sharpe",
                "total_return",
                "max_ddpercent",
                "return_drawdown_ratio",
                "trades",
            ],
            top_rows,
        )
    )

    print("\nreport:", out)
    print("csv   :", csv_path)


if __name__ == "__main__":
    main()
