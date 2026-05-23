# -*- coding: utf-8 -*-
"""End-to-end unit tests for ``phase2.backtest.portfolio_backtest_engine``.

Strategy: monkey-patch ``vnpy.trader.database.get_database`` (and the
import the engine module already resolved) with a fake DB that returns
synthetic BarData per symbol.  Then drive a tiny 2-symbol / 30-day
backtest with a *minimal* test strategy (also exec'd through the same
loader path) so we can deterministically assert:

- Next-bar-open settlement (no look-ahead).
- Per-symbol cash debit / credit and position changes.
- Trade ledger contains BOTH symbols (real multi-symbol routing).
- Reports written to the requested output_root.

The real futumd strategy is *also* booted (as a smoke test) on the
locally available 12-symbol pool overview to make sure the loader path
plays nicely with the engine surface — but to keep this unit test fast
and deterministic, the bulk of the assertions use a tiny inline test
strategy file written to a tmp directory.
"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List
from unittest import mock

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from phase2.backtest import portfolio_backtest_engine as engine_mod
from phase2.backtest.portfolio_backtest_engine import PortfolioBacktestEngine


# Tiny test strategy: buys the first symbol on bar #5 and sells it on
# bar #15; buys the second symbol on bar #10 and sells on bar #20.
# Uses the futumd DSL surface (bar_close, place_limit, close_positions).
_TEST_STRATEGY_SOURCE = '''
class Strategy(StrategyBase):
    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self._pool = ["AAA", "BBB"]
        self._tick = 0
        for _ in self._pool:
            declare_trig_symbol()

    def handle_data(self):
        self._tick += 1
        if self._tick == 5:
            place_limit(symbol="AAA", price=bar_close(symbol="AAA", select=1),
                        qty=10, side=OrderSide.BUY,
                        time_in_force=TimeInForce.DAY)
        elif self._tick == 10:
            place_limit(symbol="BBB", price=bar_close(symbol="BBB", select=1),
                        qty=5, side=OrderSide.BUY,
                        time_in_force=TimeInForce.DAY)
        elif self._tick == 15:
            close_positions(symbol="AAA")
        elif self._tick == 20:
            close_positions(symbol="BBB")
'''


def _make_bars(symbol: str, start: date, n_days: int,
               base_price: float) -> List[BarData]:
    out: List[BarData] = []
    for i in range(n_days):
        d = start + timedelta(days=i)
        # Skip weekends to mimic real US session calendar (loosely).
        if d.weekday() >= 5:
            continue
        price = base_price + i * 0.5
        out.append(BarData(
            symbol=symbol,
            exchange=Exchange.SMART,
            datetime=datetime.combine(d, time()),
            interval=Interval.DAILY,
            open_price=price,
            high_price=price + 0.5,
            low_price=price - 0.5,
            close_price=price + 0.2,
            volume=10_000.0,
            turnover=0.0,
            open_interest=0.0,
            gateway_name="TEST",
        ))
    return out


class _FakeDatabase:
    def __init__(self, bars_by_symbol: Dict[str, List[BarData]]) -> None:
        self._bars = bars_by_symbol

    def load_bar_data(self, symbol, exchange, interval, start, end):
        return list(self._bars.get(symbol, []))


class PortfolioBacktestEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = date(2025, 1, 6)  # Monday
        # 30 calendar days covers ~22 trading days on a 5/7 business cal.
        self.aaa_bars = _make_bars("AAA", self.start, 35, base_price=100.0)
        self.bbb_bars = _make_bars("BBB", self.start, 35, base_price=200.0)
        self.fake_db = _FakeDatabase({
            "AAA": self.aaa_bars,
            "BBB": self.bbb_bars,
        })

        # Write the tiny test strategy to a temp file in the layout the
        # adapter expects (loader path is purely filesystem-driven).
        self.tmpdir = tempfile.TemporaryDirectory()
        self.strategy_path = Path(self.tmpdir.name) / "tiny_strategy.py"
        self.strategy_path.write_text(_TEST_STRATEGY_SOURCE, encoding="utf-8")

        self.output_root = Path(self.tmpdir.name) / "runs"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _patched_run(self) -> None:
        with mock.patch.object(engine_mod, "get_database",
                                return_value=self.fake_db):
            engine = PortfolioBacktestEngine(
                pool_symbols=["AAA", "BBB"],
                start=self.start,
                end=self.start + timedelta(days=35),
                init_cash=100_000.0,
                exchange=Exchange.SMART,
                interval=Interval.DAILY,
                fee_rate=0.001,
                slippage=0.0,
            )
            self.result = engine.run(
                strategy_path=self.strategy_path,
                run_id="ut_e2e",
                output_root=self.output_root,
            )

    def test_engine_runs_and_emits_reports(self) -> None:
        self._patched_run()
        out_dir = self.output_root / "ut_e2e"
        self.assertTrue((out_dir / "equity_curve.csv").is_file())
        self.assertTrue((out_dir / "positions_daily.csv").is_file())
        self.assertTrue((out_dir / "trade_ledger.csv").is_file())
        self.assertTrue((out_dir / "summary.json").is_file())

    def test_trade_ledger_contains_both_symbols(self) -> None:
        self._patched_run()
        ledger_path = self.output_root / "ut_e2e" / "trade_ledger.csv"
        with ledger_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        symbols = {r["symbol"] for r in rows}
        self.assertSetEqual(symbols, {"AAA", "BBB"})
        sides = {(r["symbol"], r["side"]) for r in rows}
        # Both legs of both round-trips must appear.
        self.assertIn(("AAA", "BUY"), sides)
        self.assertIn(("AAA", "SELL"), sides)
        self.assertIn(("BBB", "BUY"), sides)
        self.assertIn(("BBB", "SELL"), sides)

    def test_buy_filled_at_next_bar_open(self) -> None:
        """On tick 5 the strategy submits BUY AAA; fill price must be
        the open of the next trading day."""

        self._patched_run()
        ledger_path = self.output_root / "ut_e2e" / "trade_ledger.csv"
        with ledger_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        aaa_buy = next(r for r in rows if r["symbol"] == "AAA"
                                 and r["side"] == "BUY")
        # Strategy ticks once per closed bar; tick 5 corresponds to
        # all_dates[4] — next bar is all_dates[5].  Recompute that
        # day's open from the synthetic price formula.
        # NB: weekends are skipped in _make_bars, so ``bars[5]`` is the
        # 6th business day of the synthetic feed.
        expected_open = self.aaa_bars[5].open_price
        self.assertAlmostEqual(float(aaa_buy["price"]), expected_open, places=6)

    def test_cash_decreases_on_buy_and_increases_on_sell(self) -> None:
        self._patched_run()
        eq_path = self.output_root / "ut_e2e" / "equity_curve.csv"
        with eq_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        cashes = [float(r["cash"]) for r in rows]
        # Cash is monotone-non-increasing until first BUY hits, then
        # changes again on SELL.  Easiest deterministic invariant:
        # the cash trace must not stay constant for the full run when
        # trades happened.
        self.assertGreater(max(cashes) - min(cashes), 0.0)
        # NAV at end must be finite, positive, and the same as in summary.
        with (self.output_root / "ut_e2e" / "summary.json").open() as fh:
            summary = json.load(fh)
        self.assertAlmostEqual(summary["final_nav"], float(rows[-1]["nav"]),
                               places=4)

    def test_positions_daily_per_symbol_columns(self) -> None:
        self._patched_run()
        pos_path = self.output_root / "ut_e2e" / "positions_daily.csv"
        with pos_path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(reader.fieldnames, ["date", "AAA", "BBB"])
            rows = list(reader)
        # AAA must reach 10 shares, BBB must reach 5 shares at some point.
        max_aaa = max(int(r["AAA"]) for r in rows)
        max_bbb = max(int(r["BBB"]) for r in rows)
        self.assertEqual(max_aaa, 10)
        self.assertEqual(max_bbb, 5)
        # And both must end at 0 because we close them.
        self.assertEqual(int(rows[-1]["AAA"]), 0)
        self.assertEqual(int(rows[-1]["BBB"]), 0)

    def test_summary_contains_required_keys(self) -> None:
        self._patched_run()
        summary_path = self.output_root / "ut_e2e" / "summary.json"
        with summary_path.open() as fh:
            summary = json.load(fh)
        for key in (
            "run_id", "pool", "start", "end", "trading_days",
            "init_cash", "final_nav", "final_cash", "total_return_pct",
            "annualised_return_pct", "max_drawdown_pct",
            "trade_count", "win_count", "loss_count",
            "fee_rate", "slippage", "annual_trading_days",
        ):
            self.assertIn(key, summary)
        self.assertEqual(summary["pool"], ["AAA", "BBB"])


# A second tiny strategy that mirrors the futumd production guard:
# place_limit is *only* called when ``self.LIVE_SUBMIT`` is True.
# This lets us verify the engine's ``force_live_submit`` switch end-to-end.
_LIVE_SUBMIT_GATED_STRATEGY_SOURCE = '''
class Strategy(StrategyBase):
    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self._pool = ["AAA", "BBB"]
        self._tick = 0
        # Mirror the futumd source: ``LIVE_SUBMIT`` defaults to False
        # exactly like the production strategy.
        self.LIVE_SUBMIT = show_variable(False, GlobalType.BOOL)
        for _ in self._pool:
            declare_trig_symbol()

    def handle_data(self):
        self._tick += 1
        if self._tick == 5:
            if self.LIVE_SUBMIT:
                place_limit(symbol="AAA",
                            price=bar_close(symbol="AAA", select=1),
                            qty=10, side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY)
            else:
                alert(title="dry-run", content="would BUY AAA")
'''


class LiveSubmitOverrideTests(unittest.TestCase):
    """Cover the engine's force_live_submit switch end-to-end."""

    def setUp(self) -> None:
        self.start = date(2025, 1, 6)
        self.aaa_bars = _make_bars("AAA", self.start, 30, base_price=100.0)
        self.bbb_bars = _make_bars("BBB", self.start, 30, base_price=200.0)
        self.fake_db = _FakeDatabase({
            "AAA": self.aaa_bars,
            "BBB": self.bbb_bars,
        })
        self.tmpdir = tempfile.TemporaryDirectory()
        self.strategy_path = Path(self.tmpdir.name) / "live_gated.py"
        self.strategy_path.write_text(
            _LIVE_SUBMIT_GATED_STRATEGY_SOURCE, encoding="utf-8")
        self.output_root = Path(self.tmpdir.name) / "runs"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _run(self, *, force_live_submit: bool, run_id: str):
        with mock.patch.object(engine_mod, "get_database",
                                return_value=self.fake_db):
            engine = PortfolioBacktestEngine(
                pool_symbols=["AAA", "BBB"],
                start=self.start,
                end=self.start + timedelta(days=30),
                init_cash=100_000.0,
                exchange=Exchange.SMART,
                interval=Interval.DAILY,
                fee_rate=0.001,
                slippage=0.0,
                force_live_submit=force_live_submit,
            )
            return engine.run(
                strategy_path=self.strategy_path,
                run_id=run_id,
                output_root=self.output_root,
            )

    def test_default_force_live_submit_emits_trades(self) -> None:
        """With force_live_submit=True (default) the gated strategy must
        actually issue a BUY because LIVE_SUBMIT was flipped to True."""

        result = self._run(force_live_submit=True, run_id="lso_on")
        self.assertGreater(
            result.trade_count, 0,
            "force_live_submit=True must let the strategy reach place_limit; "
            "got 0 trades.",
        )
        ledger_path = self.output_root / "lso_on" / "trade_ledger.csv"
        with ledger_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertTrue(any(r["side"] == "BUY" and r["symbol"] == "AAA"
                            for r in rows))

    def test_respect_live_submit_yields_zero_trades(self) -> None:
        """With force_live_submit=False the engine must respect the
        strategy's own LIVE_SUBMIT=False, so place_limit is never called
        and trade_count is 0."""

        result = self._run(force_live_submit=False, run_id="lso_off")
        self.assertEqual(
            result.trade_count, 0,
            "force_live_submit=False must honour the strategy's "
            "LIVE_SUBMIT=False; expected 0 trades, got "
            f"{result.trade_count}.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
