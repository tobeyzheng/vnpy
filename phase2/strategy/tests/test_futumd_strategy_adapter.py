# -*- coding: utf-8 -*-
"""Unit tests for ``phase2.backtest.futumd_strategy_adapter``.

Verifies the per-symbol bucketing contract:
- ``bar_close(symbol=)`` and friends route reads to the right bucket.
- ``cash`` / ``net_asset`` are portfolio-scoped (single shared pool).
- ``position_holding_qty(symbol=)`` returns the correct per-symbol holding.
- ``place_limit(symbol=)`` enqueues an intent tagged with the right symbol.
- ``close_positions(symbol=)`` clamps to the current holding.
- ``settle_pending`` honours next-bar-open fills, fees, slippage,
  cash buffer, and short-sale prevention.
- ``load_futumd_strategy`` boots the real strategy with the DSL injected
  and ``Strategy.initialize()`` succeeds without touching real Futu APIs.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from phase2.backtest.futumd_strategy_adapter import (
    PortfolioRuntime,
    build_futumd_namespace,
    load_futumd_strategy,
    settle_pending,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_FUTUMD_PATH = (
    _REPO_ROOT / "phase2" / "strategy"
    / "us_multi_symbol_phase2_strategy_futumd.py"
)


class PortfolioRuntimeBucketingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.symbols = ["AAPL", "NVDA", "MSFT"]
        self.runtime = PortfolioRuntime(
            symbols=list(self.symbols),
            cash_value=100_000.0,
        )
        self.ns = build_futumd_namespace(self.runtime)

        # Push 5 bars per symbol with deterministic per-symbol prices so
        # any namespace-level cross-talk is immediately observable.
        for day in range(5):
            self.runtime.bar_index = day
            for offset, sym in enumerate(self.symbols):
                base = (offset + 1) * 100.0
                price = base + day  # AAPL: 100..104, NVDA: 200..204, MSFT: 300..304
                self.runtime.push_bar(
                    sym, o=price, h=price + 1, l=price - 1,
                    c=price, v=1_000.0 * (offset + 1),
                )

    def test_bar_close_routes_per_symbol(self) -> None:
        bar_close = self.ns["bar_close"]
        # select=1 means the most recent closed bar.
        self.assertAlmostEqual(bar_close(symbol="AAPL", select=1), 104.0)
        self.assertAlmostEqual(bar_close(symbol="NVDA", select=1), 204.0)
        self.assertAlmostEqual(bar_close(symbol="MSFT", select=1), 304.0)
        # select=2 → previous bar.
        self.assertAlmostEqual(bar_close(symbol="AAPL", select=2), 103.0)
        self.assertAlmostEqual(bar_close(symbol="NVDA", select=3), 202.0)

    def test_bar_close_unknown_symbol_returns_zero(self) -> None:
        self.assertEqual(self.ns["bar_close"](symbol="UNKNOWN", select=1), 0.0)

    def test_bar_close_select_out_of_range(self) -> None:
        # only 5 bars pushed; select=99 must return 0.0 (futumd contract).
        self.assertEqual(self.ns["bar_close"](symbol="AAPL", select=99), 0.0)

    def test_bar_high_low_volume_routing(self) -> None:
        # MSFT high at the latest bar = 304 + 1 = 305, low = 303, volume = 3000.
        self.assertAlmostEqual(self.ns["bar_high"](symbol="MSFT", select=1), 305.0)
        self.assertAlmostEqual(self.ns["bar_low"](symbol="MSFT", select=1), 303.0)
        self.assertAlmostEqual(self.ns["bar_volume"](symbol="MSFT", select=1), 3000.0)

    def test_cash_is_portfolio_scoped(self) -> None:
        # Cash must NOT depend on symbol — single shared pool.
        c1 = self.ns["cash"]()
        c2 = self.ns["cash"]()
        self.assertEqual(c1, 100_000.0)
        self.assertEqual(c2, 100_000.0)

    def test_net_asset_marks_to_market(self) -> None:
        # Hand-set positions to exercise NAV.
        self.runtime.positions["AAPL"] = 10
        self.runtime.positions["NVDA"] = 5
        # latest closes: AAPL=104, NVDA=204
        expected = 100_000.0 + 10 * 104.0 + 5 * 204.0
        self.assertAlmostEqual(self.ns["net_asset"](), expected)

    def test_position_holding_qty_per_symbol(self) -> None:
        self.runtime.positions["AAPL"] = 7
        self.runtime.positions["NVDA"] = 0
        self.assertEqual(self.ns["position_holding_qty"](symbol="AAPL"), 7)
        self.assertEqual(self.ns["position_holding_qty"](symbol="NVDA"), 0)
        self.assertEqual(self.ns["position_holding_qty"](symbol="UNKNOWN"), 0)

    def test_place_limit_tags_correct_symbol(self) -> None:
        ns = self.ns
        ns["place_limit"](symbol="AAPL", price=110.0, qty=10,
                          side=ns["OrderSide"].BUY,
                          time_in_force=ns["TimeInForce"].DAY)
        ns["place_limit"](symbol="NVDA", price=220.0, qty=3,
                          side=ns["OrderSide"].BUY)
        self.assertEqual(len(self.runtime.pending), 2)
        self.assertEqual(self.runtime.pending[0].symbol, "AAPL")
        self.assertEqual(self.runtime.pending[0].action, "BUY")
        self.assertEqual(self.runtime.pending[0].qty, 10)
        self.assertEqual(self.runtime.pending[1].symbol, "NVDA")
        self.assertEqual(self.runtime.pending[1].qty, 3)

    def test_place_limit_rejects_invalid_inputs(self) -> None:
        ns = self.ns
        ns["place_limit"](symbol="", price=100.0, qty=10, side=ns["OrderSide"].BUY)
        ns["place_limit"](symbol="AAPL", price=0.0, qty=10, side=ns["OrderSide"].BUY)
        ns["place_limit"](symbol="AAPL", price=100.0, qty=0, side=ns["OrderSide"].BUY)
        self.assertEqual(self.runtime.pending, [])

    def test_close_positions_clamps_to_holding(self) -> None:
        self.runtime.positions["AAPL"] = 5
        self.ns["close_positions"](symbol="AAPL", qty=999)
        self.assertEqual(len(self.runtime.pending), 1)
        self.assertEqual(self.runtime.pending[0].action, "SELL_CLOSE")
        self.assertEqual(self.runtime.pending[0].qty, 5)
        self.assertEqual(self.runtime.pending[0].symbol, "AAPL")

    def test_close_positions_no_position_drops(self) -> None:
        self.runtime.positions["AAPL"] = 0
        self.ns["close_positions"](symbol="AAPL", qty=10)
        self.assertEqual(self.runtime.pending, [])

    def test_alert_captured(self) -> None:
        self.ns["alert"](title="t1", content="c1")
        self.assertEqual(self.runtime.alerts, [("t1", "c1")])


class SettlePendingTests(unittest.TestCase):
    def _runtime(self, cash: float = 100_000.0) -> PortfolioRuntime:
        rt = PortfolioRuntime(symbols=["AAPL", "NVDA"], cash_value=cash)
        for sym in rt.symbols:
            rt.push_bar(sym, 100.0, 101.0, 99.0, 100.0, 1_000.0)
        return rt

    def test_buy_fill_consumes_cash_and_records_position(self) -> None:
        rt = self._runtime(cash=10_000.0)
        ns = build_futumd_namespace(rt)
        ns["place_limit"](symbol="AAPL", price=100.0, qty=10,
                          side=ns["OrderSide"].BUY)

        next_open = {"AAPL": 102.0, "NVDA": 202.0}
        trades = settle_pending(
            rt,
            fill_price_for_symbol=lambda s: next_open.get(s),
            fee_rate=0.001,
            slippage=0.0,
            bar_index=1,
            bar_datetime="2026-01-02T00:00:00",
        )
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t.symbol, "AAPL")
        self.assertEqual(t.side, "BUY")
        self.assertEqual(t.qty, 10)
        self.assertAlmostEqual(t.price, 102.0)
        self.assertAlmostEqual(t.fee, 102.0 * 10 * 0.001)
        # Cash decremented by notional + fee.
        self.assertAlmostEqual(
            rt.cash_value, 10_000.0 - (102.0 * 10 + 102.0 * 10 * 0.001)
        )
        self.assertEqual(rt.positions["AAPL"], 10)
        self.assertAlmostEqual(rt.entry_costs["AAPL"], 102.0)
        # NVDA bucket untouched.
        self.assertEqual(rt.positions["NVDA"], 0)

    def test_buy_rescaled_when_cash_short(self) -> None:
        rt = self._runtime(cash=500.0)  # tight budget
        ns = build_futumd_namespace(rt)
        ns["place_limit"](symbol="AAPL", price=100.0, qty=10,
                          side=ns["OrderSide"].BUY)
        next_open = {"AAPL": 102.0}
        trades = settle_pending(
            rt,
            fill_price_for_symbol=lambda s: next_open.get(s),
            fee_rate=0.0, slippage=0.0,
            bar_index=1, bar_datetime="d",
        )
        self.assertEqual(len(trades), 1)
        # 500 // 102 = 4 shares max.
        self.assertEqual(trades[0].qty, 4)
        self.assertGreaterEqual(rt.cash_value, 0.0)

    def test_sell_clamps_and_credits_cash(self) -> None:
        rt = self._runtime(cash=0.0)
        rt.positions["NVDA"] = 5
        rt.entry_costs["NVDA"] = 200.0
        ns = build_futumd_namespace(rt)
        ns["close_positions"](symbol="NVDA", qty=999)
        next_open = {"NVDA": 210.0}
        trades = settle_pending(
            rt,
            fill_price_for_symbol=lambda s: next_open.get(s),
            fee_rate=0.0, slippage=0.001,
            bar_index=1, bar_datetime="d",
        )
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t.side, "SELL")
        self.assertEqual(t.qty, 5)
        self.assertAlmostEqual(t.price, 210.0 * (1 - 0.001))
        self.assertEqual(rt.positions["NVDA"], 0)
        self.assertAlmostEqual(rt.entry_costs["NVDA"], 0.0)
        self.assertGreater(rt.cash_value, 0.0)

    def test_missing_next_open_drops_intent(self) -> None:
        rt = self._runtime(cash=10_000.0)
        ns = build_futumd_namespace(rt)
        ns["place_limit"](symbol="AAPL", price=100.0, qty=10,
                          side=ns["OrderSide"].BUY)
        trades = settle_pending(
            rt,
            fill_price_for_symbol=lambda s: None,  # data gap
            fee_rate=0.0, slippage=0.0,
            bar_index=1, bar_datetime="d",
        )
        self.assertEqual(trades, [])
        self.assertEqual(rt.cash_value, 10_000.0)
        self.assertEqual(rt.positions["AAPL"], 0)


class LoadFutumdStrategyTests(unittest.TestCase):
    """Boot the real futumd strategy file with DSL injected."""

    def test_strategy_initialise_uses_injected_namespace(self) -> None:
        rt = PortfolioRuntime(
            symbols=["AAPL", "NVDA", "MSFT"],
            cash_value=100_000.0,
        )
        module, strategy_cls = load_futumd_strategy(
            _FUTUMD_PATH, rt,
            module_name="phase2_futumd_strategy_unittest_t7",
        )
        self.assertTrue(hasattr(module, "Strategy"))
        strategy = strategy_cls()
        # ``initialize`` must NOT raise when DSL names are pre-injected.
        strategy.initialize()
        self.assertGreater(len(strategy._pool), 0)
        self.assertLessEqual(len(strategy._pool), 20)
        # The injected ``show_variable`` returns the value as-is, so
        # primitive parameters are usable in arithmetic.
        self.assertEqual(int(strategy.fast_window), 5)
        self.assertEqual(int(strategy.slow_window), 20)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
