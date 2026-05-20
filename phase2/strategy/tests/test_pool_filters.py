"""Unit tests for ``phase2.strategy.pool_loader`` runtime filters.

Covers Task 3 of [.codebuddy/plan/us_multi_symbol_quant_phase2/task-item.md]:
4 filter functions × (positive + negative) = 8 cases, plus the pool change
log helper.

Run:  python3 -m unittest phase2.strategy.tests.test_pool_filters -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from phase2.strategy.pool_loader import (  # noqa: E402
    PoolConfig,
    PoolDefaults,
    PoolSymbol,
    append_pool_change_log,
    apply_runtime_filters,
    filter_atr_pct,
    filter_earnings_freeze,
    filter_liquidity,
    filter_price_band,
)


def _build_cfg() -> PoolConfig:
    """Tiny in-memory pool to exercise the filter pipeline."""

    return PoolConfig(
        version=1,
        currency="USD",
        max_pool_size=20,
        defaults=PoolDefaults(
            liquidity_min_adv60_usd=50_000_000.0,
            price_min=5.0,
            price_max=800.0,
            atr_pct_max=0.08,
            earnings_freeze_days=2,
            market="US",
        ),
        symbols=[
            PoolSymbol(symbol="AAA", market="US",
                       market_cap_bucket="large", sector="Technology"),
            PoolSymbol(symbol="BBB", market="US",
                       market_cap_bucket="large", sector="Energy"),
        ],
    )


class FilterUnitTests(unittest.TestCase):
    """8 positive/negative cases — one pair per filter."""

    # ---- liquidity -------------------------------------------------------- #
    def test_liquidity_pass(self) -> None:
        ok, why = filter_liquidity(60_000_000.0, 50_000_000.0)
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_liquidity_reject_when_below_min(self) -> None:
        ok, why = filter_liquidity(40_000_000.0, 50_000_000.0)
        self.assertFalse(ok)
        self.assertEqual(why, "low_adv60")

    # ---- price band ------------------------------------------------------- #
    def test_price_band_pass(self) -> None:
        ok, why = filter_price_band(120.0, 5.0, 800.0)
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_price_band_reject_below_min(self) -> None:
        ok, why = filter_price_band(3.5, 5.0, 800.0)
        self.assertFalse(ok)
        self.assertEqual(why, "below_price_min")

    # ---- ATR% ------------------------------------------------------------- #
    def test_atr_pct_pass(self) -> None:
        ok, why = filter_atr_pct(0.04, 0.08)
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_atr_pct_reject_when_too_high(self) -> None:
        ok, why = filter_atr_pct(0.12, 0.08)
        self.assertFalse(ok)
        self.assertEqual(why, "atr_too_high")

    # ---- earnings freeze -------------------------------------------------- #
    def test_earnings_freeze_pass_outside_window(self) -> None:
        cal = {"AAPL": ["2024-02-01"]}
        ok, why = filter_earnings_freeze("AAPL", "2024-03-15", cal, 2)
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_earnings_freeze_reject_inside_window(self) -> None:
        cal = {"AAPL": ["2024-02-01"]}
        # 2024-01-31 is 1 day before earnings ⇒ within ±2 freeze window.
        ok, why = filter_earnings_freeze("AAPL", "2024-01-31", cal, 2)
        self.assertFalse(ok)
        self.assertEqual(why, "earnings_freeze")


class FilterPipelineTests(unittest.TestCase):
    """End-to-end ``apply_runtime_filters`` smoke test."""

    def test_admit_and_reject_split(self) -> None:
        cfg = _build_cfg()
        snapshot = {
            "AAA": {"price": 120.0, "adv60_usd": 80_000_000.0, "atr_pct": 0.04},
            # BBB rejected by ATR% gate
            "BBB": {"price": 60.0, "adv60_usd": 90_000_000.0, "atr_pct": 0.20},
        }
        admitted, rejected = apply_runtime_filters(
            cfg, market_snapshot=snapshot
        )
        self.assertEqual(admitted, ["AAA"])
        self.assertEqual(rejected, {"BBB": "atr_too_high"})

    def test_missing_snapshot_rejected(self) -> None:
        cfg = _build_cfg()
        admitted, rejected = apply_runtime_filters(cfg, market_snapshot={})
        self.assertEqual(admitted, [])
        self.assertEqual(rejected, {"AAA": "no_snapshot", "BBB": "no_snapshot"})


class PoolChangeLogTests(unittest.TestCase):
    """Persistent pool-change log writer."""

    def test_append_change_writes_one_line(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d) / "operation_log.md"
            append_pool_change_log(
                log_path,
                added=["AAA"],
                removed=["BBB"],
                reason="liquidity drop",
                date_iso="2026-05-20",
            )
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("2026-05-20 phase2-pool 池变更", text)
            self.assertIn("+AAA", text)
            self.assertIn("-BBB", text)
            self.assertIn("liquidity drop", text)

    def test_empty_change_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d) / "operation_log.md"
            append_pool_change_log(log_path)  # no added / removed
            self.assertFalse(log_path.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
