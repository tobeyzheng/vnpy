from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FUTU_EXAMPLE_DIR = PROJECT_ROOT.joinpath("examples", "futu_trader")
for path in [PROJECT_ROOT, FUTU_EXAMPLE_DIR]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from soxl_cta_no_ui import (  # type: ignore
    INITIAL_CAPITAL,
    VT_SYMBOL,
    BacktestStats,
    EquityRecord,
    StrategyParams,
    TradeRecord,
    apply_slippage,
    calculate_stats,
    create_main_engine,
    fetch_history,
    load_best_params,
)

from vnpy_llm.config import load_config
from vnpy_llm.risk import RiskPolicy, make_risk_decision
from vnpy_llm.signal_store import SignalStore


def backtest_cta_with_llm(
    bars,
    params: StrategyParams,
    signal_store: SignalStore,
    policy: RiskPolicy,
    capital: float = INITIAL_CAPITAL,
    commission_rate: float = 0.0003,
    slippage_bps: float = 5,
) -> tuple[BacktestStats, list[EquityRecord], list[TradeRecord]]:
    from soxl_cta_no_ui import SoxlCtaStrategy

    strategy = SoxlCtaStrategy(params)
    min_index = max(params.slow_window, params.atr_window) + 1
    cash = capital
    position = 0
    entry_price = 0.0
    highest_close = 0.0
    peak_equity = capital
    trading_stopped = False
    equity_curve: list[EquityRecord] = []
    trades: list[TradeRecord] = []
    round_trips: list[float] = []
    current_trade_cost = 0.0

    for signal_index in range(min_index, len(bars) - 1):
        signal_bar = bars[signal_index]
        trade_bar = bars[signal_index + 1]
        open_price = trade_bar.open_price or trade_bar.close_price
        close_price = trade_bar.close_price
        equity_before_trade = cash + position * signal_bar.close_price
        target_volume = position
        reason = "hold"

        if position:
            highest_close = max(highest_close, signal_bar.close_price)
            exit_now, reason = strategy.should_exit(bars, signal_index, entry_price, highest_close)
            if exit_now or trading_stopped:
                target_volume = 0
        elif not trading_stopped:
            cta_target = strategy.calc_target_volume(bars, signal_index, open_price, equity_before_trade)
            if cta_target:
                signal = signal_store.latest(VT_SYMBOL, signal_bar.datetime)
                decision = make_risk_decision(signal, policy, signal_bar.datetime)
                if decision.allow_new_long:
                    target_volume = math.floor(cta_target * decision.position_multiplier)
                    reason = f"trend_entry+llm:{decision.reason}"
                else:
                    target_volume = 0
                    reason = f"llm_block:{decision.reason}"
            else:
                target_volume = 0
                reason = "flat"

        diff = target_volume - position
        if diff:
            side = "buy" if diff > 0 else "sell"
            volume = abs(diff)
            fill_price = apply_slippage(open_price, side, slippage_bps)
            turnover = fill_price * volume
            commission = turnover * commission_rate

            if diff > 0:
                affordable = math.floor(cash / (fill_price * (1 + commission_rate)))
                volume = min(volume, affordable)
                if volume <= 0:
                    diff = 0
                else:
                    turnover = fill_price * volume
                    commission = turnover * commission_rate
                    cash -= turnover + commission
                    position += volume
                    entry_price = fill_price
                    highest_close = signal_bar.close_price
                    current_trade_cost = turnover + commission
            else:
                volume = min(volume, position)
                turnover = fill_price * volume
                commission = turnover * commission_rate
                cash += turnover - commission
                position -= volume
                if current_trade_cost:
                    round_trips.append((turnover - commission - current_trade_cost) / current_trade_cost)
                current_trade_cost = 0.0
                if position == 0:
                    entry_price = 0.0
                    highest_close = 0.0

            if diff:
                equity_after_trade = cash + position * close_price
                trades.append(
                    TradeRecord(
                        datetime=trade_bar.datetime.isoformat(),
                        side=side,
                        price=round(fill_price, 4),
                        volume=volume,
                        commission=round(commission, 4),
                        cash=round(cash, 2),
                        position=position,
                        equity=round(equity_after_trade, 2),
                        reason=reason,
                    )
                )

        equity = cash + position * close_price
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity else 0
        if drawdown >= params.max_drawdown_pct:
            trading_stopped = True
        equity_curve.append(
            EquityRecord(
                datetime=trade_bar.datetime.isoformat(),
                equity=round(equity, 2),
                cash=round(cash, 2),
                position=position,
                close_price=round(close_price, 4),
                drawdown_pct=round(drawdown * 100, 4),
            )
        )

    if position and bars:
        last_bar = bars[-1]
        fill_price = apply_slippage(last_bar.close_price, "sell", slippage_bps)
        turnover = fill_price * position
        commission = turnover * commission_rate
        cash += turnover - commission
        trades.append(
            TradeRecord(
                datetime=last_bar.datetime.isoformat(),
                side="sell",
                price=round(fill_price, 4),
                volume=position,
                commission=round(commission, 4),
                cash=round(cash, 2),
                position=0,
                equity=round(cash, 2),
                reason="final_close",
            )
        )

    return calculate_stats(equity_curve, trades, round_trips, capital), equity_curve, trades


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对比 SOXL 纯 CTA 与 CTA+LLM 过滤回测")
    parser.add_argument("--config", default="")
    parser.add_argument("--signal-dir", default="")
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--password", default="")
    parser.add_argument("--connect-wait", type=int, default=5)
    parser.add_argument("--output", default="examples/futu_trader/output/soxl_llm_backtest_compare.csv")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config or None, PROJECT_ROOT)
    signal_dir = Path(args.signal_dir) if args.signal_dir else config.signal_dir
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT.joinpath(output)

    main_engine = create_main_engine(args)
    try:
        bars = fetch_history(main_engine, args.days)
    finally:
        main_engine.close()

    params = load_best_params()
    from soxl_cta_no_ui import backtest_cta

    pure_stats, _, _ = backtest_cta(
        bars,
        params,
        capital=args.capital,
        commission_rate=args.commission_rate,
        slippage_bps=args.slippage_bps,
    )
    policy = RiskPolicy(
        min_confidence=config.min_confidence,
        stale_after_hours=config.stale_after_hours,
        high_risk_threshold=config.high_risk_threshold,
        block_risk_threshold=config.block_risk_threshold,
    )
    llm_stats, _, _ = backtest_cta_with_llm(
        bars,
        params,
        SignalStore(signal_dir),
        policy,
        capital=args.capital,
        commission_rate=args.commission_rate,
        slippage_bps=args.slippage_bps,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"variant": "pure_cta", **asdict(pure_stats)},
        {"variant": "cta_llm_filter", **asdict(llm_stats)},
    ]
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"回测对比已输出: {output}")


if __name__ == "__main__":
    main()
