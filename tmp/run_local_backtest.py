#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地回测运行脚本 - 使用 vnpy_ctastrategy BacktestingEngine + strategy_adapter

Usage:
    python3 tmp/run_local_backtest.py \\
        --strategy tmp/strategy/us_strategy_simple_multifactor2.py \\
        --symbol NVDA.SMART \\
        --interval 1d \\
        --start 2023-01-01 \\
        --end 2024-12-31 \\
        --capital 100000

数据需先下载到本地vnpy数据库（如通过 tmp/data_downloader.py）。
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup
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

from strategy_adapter import make_cta_class  # type: ignore


# ---------------------------------------------------------------------------
# Interval mapping
# ---------------------------------------------------------------------------
INTERVAL_MAP: Dict[str, Dict[str, Any]] = {
    "5m":  {"base_interval": Interval.MINUTE, "window": 1, "annual_days": 240},
    "10m": {"base_interval": Interval.MINUTE, "window": 2, "annual_days": 240},
    "30m": {"base_interval": Interval.MINUTE, "window": 6, "annual_days": 240},
    "1h":  {"base_interval": Interval.HOUR,   "window": 1, "annual_days": 240},
    "4h":  {"base_interval": Interval.HOUR,   "window": 4, "annual_days": 240},
    "1d":  {"base_interval": Interval.DAILY,  "window": 1, "annual_days": 240},
}


def resample_bars(bars: List[BarData], window: int) -> List[BarData]:
    """Aggregate *window* consecutive bars into one (same as run_optimize.py)."""
    if window <= 1 or not bars:
        return list(bars)

    out: List[BarData] = []
    bucket: List[BarData] = []
    for b in bars:
        bucket.append(b)
        if len(bucket) == window:
            agg = BarData(
                symbol=bucket[0].symbol,
                exchange=bucket[0].exchange,
                datetime=bucket[0].datetime,
                interval=bucket[0].interval,
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
    return out


def load_bars_from_db(
    symbol: str, exchange: Exchange, interval: Interval,
    start: datetime, end: datetime,
) -> List[BarData]:
    db = get_database()
    bars = db.load_bar_data(symbol, exchange, interval, start, end)
    return list(bars)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Local backtest runner for Futu DSL strategies (vnpy CTA engine)."
    )
    p.add_argument(
        "--strategy", required=True,
        help="Path to the Futu DSL strategy file "
             "(e.g. tmp/strategy/us_strategy_simple_multifactor2.py)",
    )
    p.add_argument(
        "--symbol", required=True,
        help="vt_symbol format, e.g. NVDA.SMART, 000300.SSE",
    )
    p.add_argument(
        "--interval", default="1d",
        choices=list(INTERVAL_MAP.keys()),
        help="Bar interval (default: 1d)",
    )
    p.add_argument("--start", default="2023-01-01", help="Backtest start date")
    p.add_argument("--end", default="2024-12-31", help="Backtest end date")
    p.add_argument("--capital", type=float, default=100_000.0, help="Initial capital")
    p.add_argument("--rate", type=float, default=0.0003, help="Commission rate")
    p.add_argument("--slippage", type=float, default=0.0, help="Slippage")
    p.add_argument("--size", type=float, default=1.0, help="Contract multiplier")
    p.add_argument("--pricetick", type=float, default=0.01, help="Minimum price tick")
    p.add_argument(
        "--setting", default=None,
        help="JSON string of strategy parameter overrides, "
             "e.g. '{\"fast_window\": 10, \"rsi_oversold\": 25.0}'",
    )
    p.add_argument("--output", default=None, help="Output file path for results JSON")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # ---- Resolve strategy path ----
    strategy_path = Path(args.strategy)
    if not strategy_path.is_absolute():
        strategy_path = _PROJECT_ROOT / strategy_path
    if not strategy_path.exists():
        print(f"❌ Strategy file not found: {strategy_path}")
        return 2

    # ---- Parse symbol / exchange ----
    parts = args.symbol.split(".")
    if len(parts) != 2:
        print(
            f"❌ Invalid symbol format '{args.symbol}', "
            f"expected SYMBOL.EXCHANGE (e.g. NVDA.SMART)"
        )
        return 2
    symbol, exchange_str = parts
    try:
        exchange = Exchange(exchange_str)
    except ValueError:
        print(f"❌ Unsupported exchange: {exchange_str}")
        return 2

    # ---- Parse dates ----
    start_dt = datetime.fromisoformat(args.start)
    end_dt = datetime.fromisoformat(args.end).replace(hour=23, minute=59, second=59)

    # ---- Interval config ----
    iconf = INTERVAL_MAP[args.interval]
    base_interval: Interval = iconf["base_interval"]
    annual_days: int = iconf["annual_days"]
    resample_window: int = iconf["window"]

    # ---- Load bars from local DB ----
    print(
        f"📥 Loading data: {args.symbol} | interval={args.interval} | "
        f"{args.start} → {args.end}"
    )
    # Load with extra range for warmup / resampling alignment
    load_start = start_dt - timedelta(days=60)
    bars = load_bars_from_db(symbol, exchange, base_interval, load_start, end_dt)
    print(f"📊 Loaded {len(bars)} base bars from database")

    if not bars:
        print(
            f"❌ No data found for {args.symbol}. "
            f"Please download data first, e.g.: "
            f"python3 tmp/data_downloader.py --symbol {symbol} --exchange {exchange_str}"
        )
        return 1

    # ---- Resample ----
    bars = resample_bars(bars, resample_window)
    print(f"📊 After resampling (window={resample_window}): {len(bars)} bars")

    # ---- Build CTA class via adapter ----
    print(f"🔧 Building CTA strategy from: {strategy_path.name}")
    cta_cls = make_cta_class(
        strategy_path=str(strategy_path),
        runtime_interval=base_interval,
        class_name=f"FutuDsl_{strategy_path.stem}_{args.interval}",
        initial_capital=args.capital,
    )

    # ---- Parse setting overrides ----
    setting: Dict[str, Any] = {}
    if args.setting:
        try:
            setting = json.loads(args.setting)
        except json.JSONDecodeError as e:
            print(f"❌ Invalid --setting JSON: {e}")
            return 2

    # Ensure LIVE_SUBMIT is True for the strategy's place_limit() to fire
    # (harmless in backtest - CTA engine routes internally, never to a broker).
    setting.setdefault("LIVE_SUBMIT", True)

    # ---- Run backtest ----
    print(f"🚀 Running backtest: {args.symbol} | capital={args.capital:,.0f}")
    engine = BacktestingEngine()

    # Handle timezone awareness (vnpy stores bars tz-aware)
    tz = bars[0].datetime.tzinfo if bars and bars[0].datetime.tzinfo else None
    _start = start_dt
    _end = end_dt
    if tz is not None:
        if _start.tzinfo is None:
            _start = _start.replace(tzinfo=tz)
        if _end.tzinfo is None:
            _end = _end.replace(tzinfo=tz)

    engine.set_parameters(
        vt_symbol=args.symbol,
        interval=base_interval,
        start=_start,
        end=_end,
        rate=args.rate,
        slippage=args.slippage,
        size=args.size,
        pricetick=args.pricetick,
        capital=int(args.capital),
        annual_days=annual_days,
    )
    engine.add_strategy(cta_cls, setting)

    # Filter bars to the exact backtest window
    end_eod = _end.replace(hour=23, minute=59, second=59)
    filtered = [b for b in bars if _start <= b.datetime <= end_eod]
    engine.history_data = filtered
    print(f"📊 Filtered bars in backtest window: {len(filtered)}")

    if not filtered:
        print("❌ No bars in the specified date range")
        return 1

    engine.run_backtesting()
    engine.calculate_result()
    stats = engine.calculate_statistics(output=False)

    if not isinstance(stats, dict):
        stats = {}

    # ---- Display results ----
    # NOTE: vnpy BacktestingEngine.calculate_statistics returns:
    #   total_return / annual_return / max_ddpercent  — already *100 (absolute %)
    #   max_drawdown / total_net_pnl / daily_net_pnl  — absolute currency values
    #   end_balance / capital                          — absolute currency values
    #   sharpe_ratio                                   — ratio
    #   total_trade_count                              — integer
    # So we use :.2f for percentage fields (not :.2% which would *100 again).
    print("\n" + "=" * 60)
    print("📊 回测结果")
    print("=" * 60)
    print(f"  标的:          {args.symbol}")
    print(f"  策略:          {strategy_path.name}")
    print(f"  周期:          {args.interval}")
    print(f"  区间:          {args.start} → {args.end}")
    print(f"  初始资金:      {stats.get('capital', args.capital):>12,.2f}")
    print(f"  最终净值:      {stats.get('end_balance', 0):>12,.2f}")
    print(f"  总收益率:      {stats.get('total_return', 0):>11.2f}%")
    print(f"  年化收益率:    {stats.get('annual_return', 0):>11.2f}%")
    print(f"  最大回撤:      {stats.get('max_drawdown', 0):>11,.2f}")
    print(f"  最大回撤(%):   {stats.get('max_ddpercent', 0):>11.2f}%")
    print(f"  夏普比率:      {stats.get('sharpe_ratio', 0):>11.2f}")
    print(f"  总交易次数:    {int(stats.get('total_trade_count', 0)):>12}")
    print(f"  日均盈亏:      {stats.get('daily_net_pnl', 0):>12,.2f}")
    print("=" * 60)

    # ---- Save results if requested ----
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = _PROJECT_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        result = {
            "strategy": str(strategy_path),
            "symbol": args.symbol,
            "interval": args.interval,
            "start": args.start,
            "end": args.end,
            "capital": args.capital,
            "setting": setting,
            "statistics": {k: str(v) for k, v in stats.items()} if stats else {},
        }
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"💾 Results saved to: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())