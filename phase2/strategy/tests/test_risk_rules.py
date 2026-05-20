"""Unit tests for ``phase2.strategy.portfolio_risk`` (Task 6).

Covers the 9 risk rules required by task-item.md task 6 (4 single-symbol +
5 portfolio) plus the circuit-breaker save / load / restart-recovery flow.

Run:  python3 -m unittest phase2.strategy.tests.test_risk_rules -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from phase2.strategy.portfolio_risk import (  # noqa: E402
    HoldingSnapshot,
    PortfolioSnapshot,
    PortfolioState,
    evaluate_all_portfolio_gates,
    load_portfolio_state,
    mark_breaker_triggered,
    portfolio_concurrent_holdings,
    portfolio_consecutive_loss,
    portfolio_daily_loss,
    portfolio_drawdown,
    portfolio_sector_cap,
    save_portfolio_state,
    should_block_new_orders,
    single_atr_breakout,
    single_hard_stop,
    single_trailing_take_profit,
    single_trend_reverse,
    state_file_path,
)


class SingleSymbolRulesTests(unittest.TestCase):
    """4 single-symbol gates × positive + negative."""

    def test_single_hard_stop(self) -> None:
        triggered, reason = single_hard_stop(100.0, 94.0, 0.05)  # -6%
        self.assertTrue(triggered)
        self.assertEqual(reason, "hard_stop")
        triggered, _ = single_hard_stop(100.0, 97.0, 0.05)  # -3%
        self.assertFalse(triggered)

    def test_single_trailing_take_profit(self) -> None:
        # +12% then -6% from peak ⇒ trigger.
        triggered, reason = single_trailing_take_profit(
            entry_price=100.0,
            highest_price=112.0,
            current_price=105.28,  # 6% drawdown from 112
            take_profit_pct=0.10,
            trailing_drawdown_pct=0.05,
        )
        self.assertTrue(triggered)
        self.assertEqual(reason, "trailing_tp")
        # Not yet armed (peak gain < take_profit_pct).
        triggered, _ = single_trailing_take_profit(
            100.0, 105.0, 100.0, 0.10, 0.05
        )
        self.assertFalse(triggered)

    def test_single_trend_reverse(self) -> None:
        triggered, reason = single_trend_reverse(98.0, 100.0)
        self.assertTrue(triggered)
        self.assertEqual(reason, "trend_reverse")
        triggered, _ = single_trend_reverse(101.0, 100.0)
        self.assertFalse(triggered)

    def test_single_atr_breakout(self) -> None:
        triggered, reason = single_atr_breakout(0.13, 0.08, 1.5)  # > 0.12
        self.assertTrue(triggered)
        self.assertEqual(reason, "vol_breakout")
        triggered, _ = single_atr_breakout(0.10, 0.08, 1.5)
        self.assertFalse(triggered)


class PortfolioRulesTests(unittest.TestCase):
    """5 portfolio gates × positive + negative."""

    def _snapshot(self, **kw) -> PortfolioSnapshot:
        defaults = dict(
            nav=1_000_000.0,
            cash=200_000.0,
            holdings=[
                HoldingSnapshot("AAA", "Technology", 200_000.0),
                HoldingSnapshot("BBB", "Energy", 150_000.0),
            ],
            today_pnl=0.0,
        )
        defaults.update(kw)
        return PortfolioSnapshot(**defaults)

    def test_portfolio_drawdown(self) -> None:
        state = PortfolioState(nav_peak=1_200_000.0)
        snap = self._snapshot(nav=1_080_000.0)  # -10%
        triggered, reason = portfolio_drawdown(state, snap, 0.08)
        self.assertTrue(triggered)
        self.assertEqual(reason, "portfolio_drawdown")
        # Within limit.
        triggered, _ = portfolio_drawdown(state, self._snapshot(nav=1_150_000.0), 0.08)
        self.assertFalse(triggered)

    def test_portfolio_sector_cap(self) -> None:
        snap = self._snapshot(
            holdings=[
                HoldingSnapshot("AAA", "Technology", 300_000.0),
                HoldingSnapshot("BBB", "Technology", 200_000.0),  # 50% > 40%
                HoldingSnapshot("CCC", "Energy", 100_000.0),
            ]
        )
        triggered, reason = portfolio_sector_cap(snap, 0.40)
        self.assertTrue(triggered)
        self.assertTrue(reason.startswith("sector_cap:Technology"))
        # Within cap.
        triggered, _ = portfolio_sector_cap(self._snapshot(), 0.40)
        self.assertFalse(triggered)

    def test_portfolio_concurrent_holdings(self) -> None:
        snap = self._snapshot(
            holdings=[
                HoldingSnapshot(f"S{i}", "Tech", 10_000.0) for i in range(6)
            ]
        )
        triggered, reason = portfolio_concurrent_holdings(snap, 5)
        self.assertTrue(triggered)
        self.assertEqual(reason, "too_many_holdings")
        triggered, _ = portfolio_concurrent_holdings(self._snapshot(), 5)
        self.assertFalse(triggered)

    def test_portfolio_daily_loss(self) -> None:
        snap = self._snapshot(today_pnl=-40_000.0)  # -4%
        triggered, reason = portfolio_daily_loss(snap, 0.03)
        self.assertTrue(triggered)
        self.assertEqual(reason, "daily_loss_limit")
        triggered, _ = portfolio_daily_loss(self._snapshot(today_pnl=-10_000.0), 0.03)
        self.assertFalse(triggered)

    def test_portfolio_consecutive_loss(self) -> None:
        triggered, reason = portfolio_consecutive_loss(
            PortfolioState(consecutive_loss_days=5), 5
        )
        self.assertTrue(triggered)
        self.assertEqual(reason, "consecutive_loss")
        triggered, _ = portfolio_consecutive_loss(
            PortfolioState(consecutive_loss_days=2), 5
        )
        self.assertFalse(triggered)


class AggregatorTests(unittest.TestCase):
    def test_evaluate_all_returns_all_triggered_reasons(self) -> None:
        state = PortfolioState(nav_peak=1_200_000.0, consecutive_loss_days=5)
        snap = PortfolioSnapshot(
            nav=1_000_000.0,
            cash=100_000.0,
            holdings=[
                HoldingSnapshot("AAA", "Tech", 500_000.0),  # 50% > 40%
            ],
            today_pnl=-50_000.0,  # -5%
        )
        reasons = evaluate_all_portfolio_gates(
            state,
            snap,
            dd_limit=0.08,
            sector_cap=0.40,
            max_concurrent_holdings=5,
            daily_loss_limit_pct=0.03,
            max_consecutive_loss_days=5,
        )
        self.assertIn("portfolio_drawdown", reasons)
        self.assertTrue(any(r.startswith("sector_cap") for r in reasons))
        self.assertIn("daily_loss_limit", reasons)
        self.assertIn("consecutive_loss", reasons)


class CircuitBreakerPersistenceTests(unittest.TestCase):
    """State save / load / restart-recovery."""

    def test_save_and_reload_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = state_file_path(
                "us_multi_symbol_quant_phase2", "run_001", base_dir=d
            )
            state = PortfolioState(
                plan="us_multi_symbol_quant_phase2",
                run_id="run_001",
                nav_peak=1_000_000.0,
                nav_last=950_000.0,
                consecutive_loss_days=2,
                last_pnl_date="2026-05-20",
            )
            save_portfolio_state(state, path)
            self.assertTrue(path.is_file())
            reloaded = load_portfolio_state(
                path, plan=state.plan, run_id=state.run_id
            )
            self.assertEqual(reloaded.nav_peak, 1_000_000.0)
            self.assertEqual(reloaded.consecutive_loss_days, 2)
            self.assertFalse(reloaded.breaker_triggered)

    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nope.json"
            s = load_portfolio_state(path, plan="p", run_id="r")
            self.assertEqual(s.plan, "p")
            self.assertEqual(s.run_id, "r")
            self.assertFalse(s.breaker_triggered)

    def test_breaker_persists_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "portfolio_state.json"
            state = PortfolioState(
                plan="p", run_id="r", nav_peak=1_000_000.0
            )
            mark_breaker_triggered(state, reason="portfolio_drawdown")
            save_portfolio_state(state, path)

            # Simulate restart — fresh load.
            reloaded = load_portfolio_state(path, plan="p", run_id="r")
            self.assertTrue(reloaded.breaker_triggered)
            self.assertEqual(reloaded.breaker_reason, "portfolio_drawdown")
            self.assertTrue(should_block_new_orders(reloaded))

    def test_mark_is_idempotent_keeps_first_reason(self) -> None:
        s = PortfolioState()
        mark_breaker_triggered(s, reason="first")
        first_at = s.breaker_triggered_at
        mark_breaker_triggered(s, reason="second")
        self.assertEqual(s.breaker_reason, "first")
        self.assertEqual(s.breaker_triggered_at, first_at)

    def test_corrupt_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "portfolio_state.json"
            path.write_text("{ not valid json", encoding="utf-8")
            s = load_portfolio_state(path, plan="p", run_id="r")
            self.assertFalse(s.breaker_triggered)
            self.assertEqual(s.plan, "p")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
