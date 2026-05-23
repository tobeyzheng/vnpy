# -*- coding: utf-8 -*-
"""Date-union driven multi-symbol backtest engine for phase-② futumd strategy.

Drive loop (per trading day, in chronological order):

1. ``runtime.bar_index`` advances to today.
2. For each pool symbol that has a closed bar today, push that bar to
   the per-symbol bucket via ``runtime.push_bar``.
3. Call ``strategy.handle_data()`` — which reads via the injected DSL
   closures (``bar_close(symbol=...)`` etc.) and may enqueue intents
   into ``runtime.pending`` via ``place_limit`` / ``close_positions``.
4. Look up tomorrow's open for each pending order's symbol.  Settle
   each pending order using ``futumd_strategy_adapter.settle_pending``;
   drop orders whose symbol has no bar on the next session (e.g. last
   day of the backtest, or symbol-specific data gaps).
5. Record an end-of-day equity snapshot using mark-to-market closes.

The engine writes 4 reports under ``state/runs/phase2_multi_backtest/<run_id>/``:

- ``equity_curve.csv``       — daily NAV / cash / position_value
- ``positions_daily.csv``    — wide-format daily holdings per symbol
- ``trade_ledger.csv``       — every fill with fee
- ``summary.json``           — pool, params, totals, return / DD / hit-rate

Bound to the local vnpy database via ``vnpy.trader.database.get_database``.
NEVER connects to OpenD or any remote service.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData

from .futumd_strategy_adapter import (
    PortfolioRuntime,
    TradeRecord,
    load_futumd_strategy,
    settle_pending,
)


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #


@dataclass
class BacktestResult:
    run_id: str
    output_dir: Path
    pool: List[str]
    init_cash: float
    final_nav: float
    final_cash: float
    total_return_pct: float
    annualised_return_pct: float
    max_drawdown_pct: float
    trade_count: int
    win_count: int
    loss_count: int
    daily_equity: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[Dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


@dataclass
class PortfolioBacktestEngine:
    """Run a multi-symbol date-union backtest with next-bar-open fills."""

    pool_symbols: List[str]
    start: date
    end: date
    init_cash: float = 1_000_000.0
    exchange: Exchange = Exchange.SMART
    interval: Interval = Interval.DAILY
    fee_rate: float = 0.0003
    slippage: float = 0.0
    annual_trading_days: int = 252

    # Backtest-only override: futumd strategies ship `LIVE_SUBMIT=False`
    # as a hard production guard so that running them as scripts on the
    # Futu sandbox cannot accidentally place real orders.  In a *local*
    # backtest, however, `place_limit` is intercepted by
    # ``futumd_strategy_adapter.place_limit`` and only ever appended to
    # ``runtime.pending`` — it cannot reach OpenD or any broker.  To make
    # the local backtest actually *see* the strategy's intended trades,
    # we flip ``strategy.LIVE_SUBMIT`` to True right after
    # ``strategy.initialize()``.  Set this to False to honour whatever
    # value the strategy itself produced (useful when you want to verify
    # the "alert-only" branch.)
    force_live_submit: bool = True

    # Internals (populated by run()).
    _runtime: Optional[PortfolioRuntime] = field(default=None, init=False, repr=False)
    _trades: List[TradeRecord] = field(default_factory=list, init=False, repr=False)
    _equity_curve: List[Dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _positions_curve: List[Dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    # ------------------------------------------------------------------ #
    # Public entrypoint
    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        strategy_path: str | Path,
        run_id: str,
        output_root: str | Path = "state/runs/phase2_multi_backtest",
    ) -> BacktestResult:
        if not self.pool_symbols:
            raise ValueError("pool_symbols is empty.")
        if self.start > self.end:
            raise ValueError(
                f"start ({self.start}) must be <= end ({self.end})."
            )

        # 1. Load all bars from the local vnpy database.
        bars_by_symbol = self._load_all_bars()

        # 2. Build the chronological union of trading dates across the pool.
        all_dates: List[date] = sorted({
            b.datetime.date()
            for sym_bars in bars_by_symbol.values()
            for b in sym_bars
        })
        if not all_dates:
            raise RuntimeError(
                f"no bars found for pool={self.pool_symbols} between "
                f"{self.start} and {self.end}; check the local database."
            )
        date_to_bar: Dict[str, Dict[date, BarData]] = {
            sym: {b.datetime.date(): b for b in lst}
            for sym, lst in bars_by_symbol.items()
        }

        logger.info(
            "loaded %d symbols, %d trading days (%s -> %s)",
            len(bars_by_symbol), len(all_dates), all_dates[0], all_dates[-1],
        )

        # 3. Build runtime + strategy.
        runtime = PortfolioRuntime(
            symbols=list(self.pool_symbols),
            cash_value=float(self.init_cash),
        )
        self._runtime = runtime
        _module, strategy_cls = load_futumd_strategy(
            strategy_path,
            runtime,
            module_name=f"phase2_futumd_strategy_{run_id}",
        )
        strategy = strategy_cls()
        strategy.initialize()

        # 3.1 Backtest-only override of the production hard-gate.
        # See the docstring on ``force_live_submit`` for the full
        # rationale.  The adapter's ``place_limit`` cannot place real
        # orders, so flipping this flag here is purely a dev-loop affordance.
        if self.force_live_submit and hasattr(strategy, "LIVE_SUBMIT"):
            logger.info(
                "force_live_submit=True: setting strategy.LIVE_SUBMIT=True "
                "so the backtest can observe place_limit intents (no real "
                "orders are issued — the adapter only writes to memory)."
            )
            strategy.LIVE_SUBMIT = True

        # 4. Date-union driver.
        n_days = len(all_dates)
        for i, today in enumerate(all_dates):
            runtime.bar_index = i

            # 4.1 Push today's closed bars into each symbol's bucket.
            for sym in self.pool_symbols:
                bar = date_to_bar.get(sym, {}).get(today)
                if bar is None:
                    continue
                runtime.push_bar(
                    symbol=sym,
                    o=float(bar.open_price),
                    h=float(bar.high_price),
                    l=float(bar.low_price),
                    c=float(bar.close_price),
                    v=float(bar.volume),
                )

            # 4.2 Strategy decides on today's information set.
            try:
                strategy.handle_data()
            except Exception:  # pragma: no cover - surface strategy bugs early
                logger.exception("strategy.handle_data() raised on %s", today)
                raise

            # 4.3 Settle pending orders against the *next* session's open
            #     to avoid look-ahead bias.  On the last bar there is no
            #     next session, so any pending intents are dropped (which
            #     matches the "you cannot trade after the close of the
            #     final bar" reality).
            if i + 1 < n_days:
                next_day = all_dates[i + 1]

                def _next_open(symbol: str) -> Optional[float]:
                    bar = date_to_bar.get(symbol, {}).get(next_day)
                    if bar is None:
                        return None
                    return float(bar.open_price)

                bar_dt_iso = datetime.combine(next_day, time()).isoformat(timespec="seconds")
                fills = settle_pending(
                    runtime,
                    fill_price_for_symbol=_next_open,
                    fee_rate=self.fee_rate,
                    slippage=self.slippage,
                    bar_index=i + 1,
                    bar_datetime=bar_dt_iso,
                )
                self._trades.extend(fills)
            else:
                # Last bar: drop any unfilled intents.
                runtime.pending.clear()

            # 4.4 End-of-day equity / position snapshot (mark-to-close).
            self._equity_curve.append({
                "date": today.isoformat(),
                "cash": round(runtime.cash_value, 4),
                "position_value": round(
                    runtime.net_asset_value() - runtime.cash_value, 4
                ),
                "nav": round(runtime.net_asset_value(), 4),
            })
            row: Dict[str, Any] = {"date": today.isoformat()}
            for sym in self.pool_symbols:
                row[sym] = int(runtime.positions.get(sym, 0))
            self._positions_curve.append(row)

        # 5. Summarise + write reports.
        return self._finalise(run_id, Path(output_root))

    # ------------------------------------------------------------------ #
    # Data loading
    # ------------------------------------------------------------------ #

    def _load_all_bars(self) -> Dict[str, List[BarData]]:
        db = get_database()
        start_dt = datetime.combine(self.start, time())
        end_dt = datetime.combine(self.end, time(23, 59, 59))
        out: Dict[str, List[BarData]] = {}
        for sym in self.pool_symbols:
            bars = list(db.load_bar_data(
                sym, self.exchange, self.interval, start_dt, end_dt,
            ))
            if not bars:
                logger.warning(
                    "no bars for %s between %s and %s — symbol is excluded.",
                    sym, self.start, self.end,
                )
                continue
            bars.sort(key=lambda b: b.datetime)
            out[sym] = bars
        return out

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #

    def _finalise(self, run_id: str, output_root: Path) -> BacktestResult:
        out_dir = output_root / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        runtime = self._runtime
        assert runtime is not None  # for type checkers

        # equity_curve.csv ----------------------------------------------- #
        eq_path = out_dir / "equity_curve.csv"
        with eq_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["date", "cash", "position_value", "nav"])
            w.writeheader()
            w.writerows(self._equity_curve)

        # positions_daily.csv -------------------------------------------- #
        pos_path = out_dir / "positions_daily.csv"
        with pos_path.open("w", encoding="utf-8", newline="") as fh:
            fields = ["date"] + list(self.pool_symbols)
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(self._positions_curve)

        # trade_ledger.csv ----------------------------------------------- #
        trade_path = out_dir / "trade_ledger.csv"
        trades_dump: List[Dict[str, Any]] = []
        with trade_path.open("w", encoding="utf-8", newline="") as fh:
            fields = ["bar_index", "bar_datetime", "symbol", "side",
                      "qty", "price", "fee"]
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for t in self._trades:
                row = {
                    "bar_index": t.bar_index,
                    "bar_datetime": t.bar_datetime,
                    "symbol": t.symbol,
                    "side": t.side,
                    "qty": t.qty,
                    "price": round(t.price, 6),
                    "fee": round(t.fee, 6),
                }
                w.writerow(row)
                trades_dump.append(row)

        # summary metrics ------------------------------------------------ #
        init_cash = float(self.init_cash)
        final_nav = self._equity_curve[-1]["nav"] if self._equity_curve else init_cash
        total_ret = (final_nav / init_cash - 1.0) * 100 if init_cash > 0 else 0.0

        n_days = len(self._equity_curve)
        if n_days > 1 and init_cash > 0:
            ann_factor = self.annual_trading_days / n_days
            ann_ret = ((final_nav / init_cash) ** ann_factor - 1.0) * 100
        else:
            ann_ret = 0.0

        peak = init_cash
        max_dd = 0.0
        for row in self._equity_curve:
            nav = float(row["nav"])
            if nav > peak:
                peak = nav
            if peak > 0:
                dd = (peak - nav) / peak
                if dd > max_dd:
                    max_dd = dd

        win, loss = self._compute_round_trip_winloss()
        summary = {
            "run_id": run_id,
            "pool": list(self.pool_symbols),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "trading_days": n_days,
            "init_cash": init_cash,
            "final_nav": round(final_nav, 4),
            "final_cash": round(self._equity_curve[-1]["cash"], 4) if self._equity_curve else init_cash,
            "total_return_pct": round(total_ret, 4),
            "annualised_return_pct": round(ann_ret, 4),
            "max_drawdown_pct": round(max_dd * 100.0, 4),
            "trade_count": len(self._trades),
            "win_count": win,
            "loss_count": loss,
            "fee_rate": self.fee_rate,
            "slippage": self.slippage,
            "annual_trading_days": self.annual_trading_days,
        }
        summary_path = out_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info("backtest finished: %s", summary_path)
        return BacktestResult(
            run_id=run_id,
            output_dir=out_dir,
            pool=list(self.pool_symbols),
            init_cash=init_cash,
            final_nav=float(summary["final_nav"]),
            final_cash=float(summary["final_cash"]),
            total_return_pct=float(summary["total_return_pct"]),
            annualised_return_pct=float(summary["annualised_return_pct"]),
            max_drawdown_pct=float(summary["max_drawdown_pct"]),
            trade_count=int(summary["trade_count"]),
            win_count=win,
            loss_count=loss,
            daily_equity=list(self._equity_curve),
            trades=trades_dump,
        )

    def _compute_round_trip_winloss(self) -> Tuple[int, int]:
        """Pair BUYs with the next SELL_CLOSE per symbol (FIFO, simple)."""

        win = 0
        loss = 0
        per_symbol: Dict[str, List[Tuple[int, float]]] = {}
        for t in self._trades:
            if t.side == "BUY":
                per_symbol.setdefault(t.symbol, []).append((t.qty, t.price))
            elif t.side == "SELL":
                lots = per_symbol.get(t.symbol, [])
                remaining = t.qty
                proceeds_price = t.price
                while remaining > 0 and lots:
                    qty0, px0 = lots[0]
                    take = min(qty0, remaining)
                    if proceeds_price > px0:
                        win += 1
                    elif proceeds_price < px0:
                        loss += 1
                    remaining -= take
                    if take >= qty0:
                        lots.pop(0)
                    else:
                        lots[0] = (qty0 - take, px0)
        return win, loss
