from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from futu import KLType, OpenQuoteContext, RET_OK, logger as futu_logger

from vnpy_llm.strategy_selector import StrategySelector

futu_logger._console_level = 50
futu_logger.console_logger.setLevel(50)
futu_logger.consoleHandler.setLevel(50)

VT_TO_FUTU_EXCHANGE = {
    "SMART": "US",
    "NYSE": "US",
    "NASDAQ": "US",
    "AMEX": "US",
    "SEHK": "HK",
    "SSE": "SH",
    "SZSE": "SZ",
}


@dataclass(frozen=True)
class BacktestSymbol:
    symbol: str
    vt_symbol: str
    futu_code: str
    name: str
    asset_type: str
    max_position_pct: float
    min_volume: int


@dataclass
class TradeRecord:
    datetime: str
    vt_symbol: str
    side: str
    price: float
    volume: int
    cash: float
    equity: float
    reason: str


@dataclass
class EquityRecord:
    datetime: str
    equity: float
    cash: float
    position_value: float
    drawdown_pct: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回测多标的LLM策略选择量化交易器最近一年表现")
    parser.add_argument("--watchlist", default="examples/futu_trader/config/quant_watchlist.json")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--include-vt-symbols", default="MSFT.SMART,INTC.SMART")
    parser.add_argument("--capital", type=float, default=10_000)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--warmup-days", type=int, default=220)
    parser.add_argument("--max-total-position-pct", type=float, default=0.3)
    parser.add_argument("--single-position-pct", type=float, default=0.08)
    parser.add_argument("--leveraged-etf-position-pct", type=float, default=0.03)
    parser.add_argument("--daily-loss-limit", type=float, default=0)
    parser.add_argument("--max-trades", type=int, default=0)
    parser.add_argument("--commission-per-order", type=float, default=1.0)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--fast-window", type=int, default=10)
    parser.add_argument("--slow-window", type=int, default=60)
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--stop-atr", type=float, default=2.5)
    parser.add_argument("--output", default="examples/futu_trader/output/backtests/watchlist_strategy_backtest.json")
    return parser


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def vt_to_futu(vt_symbol: str) -> str:
    symbol, exchange = vt_symbol.split(".", 1)
    return f"{VT_TO_FUTU_EXCHANGE[exchange]}.{symbol}"


def load_symbols(path: Path, args: argparse.Namespace) -> list[BacktestSymbol]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("selected_targets", data.get("items", []))
    filtered = [item for item in items if item.get("trade_filter") in {"allow_long", "watch_only"}]
    filtered.sort(key=lambda item: (item.get("trade_filter") == "allow_long", float(item.get("final_score", 0)), float(item.get("confidence", 0))), reverse=True)
    selected = filtered[: args.top_n]
    include_set = {item.strip().upper() for item in args.include_vt_symbols.split(",") if item.strip()}
    existing = {str(item.get("vt_symbol", "")).upper() for item in selected}
    for item in filtered:
        vt_symbol = str(item.get("vt_symbol", "")).upper()
        if vt_symbol in include_set and vt_symbol not in existing:
            selected.append(item)
            existing.add(vt_symbol)
    for vt_symbol in sorted(include_set - existing):
        selected.append({"symbol": vt_symbol.split(".", 1)[0], "vt_symbol": vt_symbol, "name": vt_symbol, "asset_type": "Stock"})

    symbols: list[BacktestSymbol] = []
    for item in selected:
        vt_symbol = str(item["vt_symbol"]).upper()
        asset_type = str(item.get("asset_type", "Stock"))
        max_pct = args.leveraged_etf_position_pct if "LEVERAGED" in asset_type.upper() else args.single_position_pct
        max_pct = min(max_pct, float(item.get("position_multiplier", max_pct)) or max_pct)
        symbol = vt_symbol.split(".", 1)[0]
        symbols.append(
            BacktestSymbol(
                symbol=symbol,
                vt_symbol=vt_symbol,
                futu_code=vt_to_futu(vt_symbol),
                name=str(item.get("name", "")),
                asset_type=asset_type,
                max_position_pct=max_pct,
                min_volume=1,
            )
        )
    return symbols


def fetch_history(ctx: OpenQuoteContext, code: str, start: date, end: date) -> list[dict[str, Any]]:
    ret, data, _ = ctx.request_history_kline(
        code,
        start=str(start),
        end=str(end),
        ktype=KLType.K_DAY,
        max_count=1000,
    )
    if ret != RET_OK:
        raise RuntimeError(f"查询历史K线失败 {code}: {data}")
    rows = data.to_dict("records")
    rows.sort(key=lambda row: row["time_key"])
    return rows


def sma(values: list[float], window: int) -> float:
    if len(values) < window:
        return 0.0
    return mean(values[-window:])


def atr(rows: list[dict[str, Any]], window: int) -> float:
    if len(rows) <= window:
        return 0.0
    trs: list[float] = []
    for i in range(len(rows) - window, len(rows)):
        high = safe_float(rows[i]["high"])
        low = safe_float(rows[i]["low"])
        prev_close = safe_float(rows[i - 1]["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return mean(trs)


def rsi(values: list[float], window: int = 14) -> float:
    if len(values) <= window:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(len(values) - window, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + mean(gains) / avg_loss)


def build_features(symbol: BacktestSymbol, rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    closes = [safe_float(row["close"]) for row in rows]
    volumes = [safe_float(row.get("volume")) for row in rows]
    price = closes[-1]
    fast = sma(closes, args.fast_window)
    slow = sma(closes, args.slow_window)
    sma20 = sma(closes, 20)
    atr_value = atr(rows, args.atr_window)
    high20 = max([safe_float(row["high"]) for row in rows[-20:]], default=0.0)
    low20 = min([safe_float(row["low"]) for row in rows[-20:]], default=0.0)
    avg_volume20 = mean(volumes[-20:]) if len(volumes) >= 20 else 0.0
    return_20d = closes[-1] / closes[-21] - 1 if len(closes) > 20 and closes[-21] else 0.0
    trend = 0.0
    trend += 0.25 if fast and price > fast else 0
    trend += 0.25 if slow and price > slow else 0
    trend += 0.25 if fast and slow and fast > slow else 0
    trend += 0.25 if return_20d > 0 else 0
    rsi14 = rsi(closes)
    risk = min((atr_value / price * 4 if price and atr_value else 0.5) + (0.15 if rsi14 > 75 else 0), 1.0)
    return {
        "symbol": symbol.symbol,
        "vt_symbol": symbol.vt_symbol,
        "asset_type": symbol.asset_type,
        "price": price,
        "fast_sma": fast,
        "slow_sma": slow,
        "sma20": sma20,
        "atr": atr_value,
        "atr_pct": atr_value / price if price and atr_value else 0.0,
        "rsi14": rsi14,
        "return_20d": return_20d,
        "volume_ratio": volumes[-1] / avg_volume20 if avg_volume20 else 1.0,
        "resistance_20d": high20,
        "support_20d": low20,
        "trend_score": trend,
        "risk_score": risk,
        "capital_score": 0.5,
    }


def target_volume(
    symbol: BacktestSymbol,
    strategy: str,
    rows: list[dict[str, Any]],
    cash: float,
    equity: float,
    current_position: int,
    args: argparse.Namespace,
) -> tuple[int, str]:
    features = build_features(symbol, rows, args)
    price = float(features["price"])
    atr_value = float(features["atr"])
    fast = float(features["fast_sma"])
    slow = float(features["slow_sma"])
    if not fast or not slow or not atr_value:
        return current_position, "warmup"

    def size(risk_fraction: float) -> int:
        total_budget = equity * args.max_total_position_pct / max(args.symbol_count, 1)
        value_limit = min(equity * symbol.max_position_pct, total_budget, cash)
        risk_limit = equity * 0.01 * risk_fraction
        if args.daily_loss_limit:
            risk_limit = min(risk_limit, args.daily_loss_limit * 0.25 * risk_fraction)
        risk_per_share = max(atr_value * args.stop_atr, price * 0.02)
        volume = min(math.floor(value_limit / price), math.floor(risk_limit / risk_per_share))
        return max(math.floor(volume / symbol.min_volume) * symbol.min_volume, 0)

    if strategy == "trend_following":
        if price <= slow or fast <= slow:
            return 0, "trend_exit"
        return size(1.0), "trend_following"
    if strategy == "breakout_momentum":
        high20 = float(features["resistance_20d"])
        volume_ratio = float(features["volume_ratio"])
        if not high20 or price < high20 * 0.985 or volume_ratio < 1.2:
            return 0, "breakout_not_confirmed"
        return size(0.7), "breakout_momentum"
    if strategy == "pullback_buy":
        sma20_value = float(features["sma20"])
        rsi_value = float(features["rsi14"])
        if not sma20_value or price > sma20_value * 1.03 or rsi_value > 68:
            return 0, "waiting_pullback"
        return size(0.5), "pullback_buy"
    return 0, strategy


def calculate_stats(equity_curve: list[EquityRecord], trades: list[TradeRecord], initial_capital: float) -> dict[str, Any]:
    if not equity_curve:
        return {}
    final_equity = equity_curve[-1].equity
    total_return = final_equity / initial_capital - 1
    daily_returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1].equity
        daily_returns.append(equity_curve[i].equity / prev - 1 if prev else 0)
    sharpe = 0.0
    if len(daily_returns) > 2 and pstdev(daily_returns) > 0:
        sharpe = mean(daily_returns) / pstdev(daily_returns) * math.sqrt(252)
    max_drawdown = max((record.drawdown_pct for record in equity_curve), default=0.0)
    years = max(len(daily_returns) / 252, 1 / 252)
    annual_return = (final_equity / initial_capital) ** (1 / years) - 1 if final_equity > 0 else -1
    return {
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return * 100, 2),
        "annual_return_pct": round(annual_return * 100, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "sharpe_ratio": round(sharpe, 3),
        "trade_count": len(trades),
        "days": len(equity_curve),
    }


def run_backtest(args: argparse.Namespace) -> dict[str, Any]:
    symbols = load_symbols(Path(args.watchlist), args)
    args.symbol_count = len(symbols)
    end = date.today()
    start = end - timedelta(days=args.days + args.warmup_days)
    test_start = end - timedelta(days=args.days)

    ctx = OpenQuoteContext("127.0.0.1", 11111)
    try:
        histories = {symbol.vt_symbol: fetch_history(ctx, symbol.futu_code, start, end) for symbol in symbols}
    finally:
        ctx.close()

    common_dates = sorted(set.intersection(*[set(row["time_key"][:10] for row in rows) for rows in histories.values() if rows]))
    common_dates = [item for item in common_dates if item >= str(test_start)]
    selector = StrategySelector(enable_llm=False)
    cash = args.capital
    positions = {symbol.vt_symbol: 0 for symbol in symbols}
    equity_curve: list[EquityRecord] = []
    trades: list[TradeRecord] = []
    peak = args.capital
    day_start_equity = args.capital

    by_date = {
        vt_symbol: {row["time_key"][:10]: row for row in rows}
        for vt_symbol, rows in histories.items()
    }

    for current_date in common_dates:
        position_value = 0.0
        for symbol in symbols:
            row = by_date[symbol.vt_symbol].get(current_date)
            if row:
                position_value += positions[symbol.vt_symbol] * safe_float(row["close"])
        equity = cash + position_value
        peak = max(peak, equity)
        equity_curve.append(
            EquityRecord(
                datetime=current_date,
                equity=equity,
                cash=cash,
                position_value=position_value,
                drawdown_pct=(peak - equity) / peak if peak else 0,
            )
        )
        day_loss = max(day_start_equity - equity, 0)
        if args.daily_loss_limit and day_loss >= args.daily_loss_limit:
            day_start_equity = equity
            continue

        for symbol in symbols:
            rows = [row for row in histories[symbol.vt_symbol] if row["time_key"][:10] <= current_date]
            if len(rows) <= max(args.slow_window, args.atr_window, 20):
                continue
            price = safe_float(rows[-1]["close"])
            features = build_features(symbol, rows, args)
            decision = selector.select(features)
            target, reason = target_volume(symbol, decision.strategy, rows, cash, equity, positions[symbol.vt_symbol], args)
            diff = target - positions[symbol.vt_symbol]
            if diff == 0:
                continue
            if args.max_trades and len(trades) >= args.max_trades:
                continue
            side = "BUY" if diff > 0 else "SELL"
            trade_price = price * (1 + args.slippage_bps / 10000 if diff > 0 else 1 - args.slippage_bps / 10000)
            volume = abs(diff)
            cost = trade_price * volume + args.commission_per_order
            if diff > 0 and cost > cash:
                volume = math.floor(max(cash - args.commission_per_order, 0) / trade_price)
                if volume <= 0:
                    continue
                cost = trade_price * volume + args.commission_per_order
                diff = volume
            if diff > 0:
                cash -= cost
                positions[symbol.vt_symbol] += volume
            else:
                sell_volume = min(volume, positions[symbol.vt_symbol])
                if sell_volume <= 0:
                    continue
                cash += trade_price * sell_volume - args.commission_per_order
                positions[symbol.vt_symbol] -= sell_volume
                volume = sell_volume
            position_value = sum(
                positions[item.vt_symbol] * safe_float(by_date[item.vt_symbol].get(current_date, {}).get("close"))
                for item in symbols
            )
            equity = cash + position_value
            trades.append(
                TradeRecord(
                    datetime=current_date,
                    vt_symbol=symbol.vt_symbol,
                    side=side,
                    price=round(trade_price, 4),
                    volume=volume,
                    cash=round(cash, 2),
                    equity=round(equity, 2),
                    reason=f"{decision.strategy}:{reason}",
                )
            )
        day_start_equity = equity

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT.joinpath(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trades_path = output_path.with_suffix(".trades.csv")
    equity_path = output_path.with_suffix(".equity.csv")
    if trades:
        with trades_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()))
            writer.writeheader()
            writer.writerows(asdict(item) for item in trades)
    if equity_curve:
        with equity_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(equity_curve[0]).keys()))
            writer.writeheader()
            writer.writerows(asdict(item) for item in equity_curve)

    result = {
        "strategy": "llm_strategy_selector_heuristic_backtest",
        "note": "回测使用无未来函数的本地启发式策略选择器；实盘可启用LLM做当前时点策略选择。",
        "symbols": [asdict(item) for item in symbols],
        "period": {"start": common_dates[0] if common_dates else "", "end": common_dates[-1] if common_dates else ""},
        "stats": calculate_stats(equity_curve, trades, args.capital),
        "outputs": {"trades": str(trades_path), "equity": str(equity_path)},
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    args = build_parser().parse_args()
    result = run_backtest(args)
    print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    print("symbols=", ",".join(item["vt_symbol"] for item in result["symbols"]))
    print("output=", args.output)


if __name__ == "__main__":
    main()
