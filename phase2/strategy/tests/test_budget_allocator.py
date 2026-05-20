"""Unit tests for ``phase2.strategy.us_multi_symbol_phase2_strategy``
budget allocator (Task 5).

Covers 4 cases per task-item.md:
1. 2 candidates with sufficient cash — both go through.
2. 3 candidates with limited cash — last one(s) skipped as
   ``insufficient_cash``.
3. ``max_orders_per_day`` cap rejects extra orders within the same bar /
   across the day.
4. Cash buffer floor rejects orders that would dip below it.

The Strategy class itself is exercised separately; here we focus on the
pure functions ``compute_per_symbol_budget`` and ``allocate_orders_serial``.

Run:  python3 -m unittest phase2.strategy.tests.test_budget_allocator -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from phase2.strategy.us_multi_symbol_phase2_strategy import (  # noqa: E402
    allocate_orders_serial,
    compute_per_symbol_budget,
)


# Defaults tuned so each slice_value == NAV * 0.80 / 5 * 0.10 == 1.6% of NAV.
_DEFAULTS = dict(
    pool_budget_pct=0.80,
    max_concurrent_holdings=5,
    position_pct=0.10,
    cash_buffer_pct=0.05,
    max_orders_per_day=10,
)


class ComputeBudgetTests(unittest.TestCase):
    """Sanity check for the per-symbol budget formula."""

    def test_per_symbol_and_slice_value(self) -> None:
        b = compute_per_symbol_budget(
            nav=1_000_000.0,
            pool_budget_pct=0.80,
            max_concurrent_holdings=5,
            position_pct=0.10,
        )
        # per_symbol = 1e6 * 0.8 / 5 = 160_000
        # slice_value = 160_000 * 0.10 = 16_000
        self.assertAlmostEqual(b["per_symbol_budget"], 160_000.0)
        self.assertAlmostEqual(b["slice_value"], 16_000.0)

    def test_invalid_inputs_raise(self) -> None:
        with self.assertRaises(ValueError):
            compute_per_symbol_budget(
                nav=-1.0,
                pool_budget_pct=0.5,
                max_concurrent_holdings=5,
                position_pct=0.1,
            )
        with self.assertRaises(ValueError):
            compute_per_symbol_budget(
                nav=1.0,
                pool_budget_pct=0.5,
                max_concurrent_holdings=0,
                position_pct=0.1,
            )


class AllocateOrdersTests(unittest.TestCase):
    """4 cases mandated by task-item.md task 5."""

    # --- Case 1: 2 candidates, sufficient cash ------------------------ #
    def test_two_candidates_with_sufficient_cash(self) -> None:
        candidates = [
            {"symbol": "AAA", "price": 100.0},
            {"symbol": "BBB", "price": 100.0},
        ]
        plan = allocate_orders_serial(
            candidates,
            nav=1_000_000.0,
            initial_cash=1_000_000.0,
            **_DEFAULTS,
        )
        # slice_value = 16_000 → qty = 160 each.
        self.assertEqual(len(plan["orders"]), 2)
        self.assertEqual(plan["skipped"], [])
        self.assertEqual(plan["orders"][0]["symbol"], "AAA")
        self.assertEqual(plan["orders"][0]["qty"], 160)
        self.assertEqual(plan["orders"][1]["qty"], 160)
        # 1_000_000 - 2 * 16_000 = 968_000.
        self.assertAlmostEqual(plan["remaining_cash"], 968_000.0)
        self.assertEqual(plan["orders_used_after"], 2)

    # --- Case 2: 3 candidates, mid-stream cash drained ---------------- #
    def test_three_candidates_third_skipped_insufficient_cash(self) -> None:
        candidates = [
            {"symbol": "AAA", "price": 100.0},
            {"symbol": "BBB", "price": 100.0},
            {"symbol": "CCC", "price": 100.0},
        ]
        # NAV = 1_000_000, slice_value = 16_000.
        # Cash starts low so that after 2 fills the spendable balance is
        # below slice_value AND below the cash buffer floor.
        # cash_floor = NAV * 0.05 = 50_000.
        plan = allocate_orders_serial(
            candidates,
            nav=1_000_000.0,
            initial_cash=80_000.0,  # only ~1.5 slices spendable above floor
            **_DEFAULTS,
        )
        self.assertEqual(len(plan["orders"]), 1)
        # Only AAA goes through; BBB and CCC are skipped insufficient_cash.
        self.assertEqual(plan["orders"][0]["symbol"], "AAA")
        self.assertEqual([s["symbol"] for s in plan["skipped"]], ["BBB", "CCC"])
        self.assertEqual(
            {s["reason"] for s in plan["skipped"]}, {"insufficient_cash"}
        )

    # --- Case 3: max_orders_per_day enforced -------------------------- #
    def test_max_orders_per_day_caps_orders(self) -> None:
        candidates = [
            {"symbol": f"S{i:02d}", "price": 100.0} for i in range(5)
        ]
        plan = allocate_orders_serial(
            candidates,
            nav=10_000_000.0,
            initial_cash=10_000_000.0,
            **{**_DEFAULTS, "max_orders_per_day": 3},
        )
        self.assertEqual(len(plan["orders"]), 3)
        self.assertEqual(len(plan["skipped"]), 2)
        self.assertEqual(
            {s["reason"] for s in plan["skipped"]}, {"max_orders_per_day"}
        )
        self.assertEqual(plan["orders_used_after"], 3)

    def test_max_orders_per_day_with_carry_in(self) -> None:
        # 9 orders already used; only 1 of 3 candidates should fit.
        candidates = [
            {"symbol": "AAA", "price": 100.0},
            {"symbol": "BBB", "price": 100.0},
            {"symbol": "CCC", "price": 100.0},
        ]
        plan = allocate_orders_serial(
            candidates,
            nav=10_000_000.0,
            initial_cash=10_000_000.0,
            orders_used_today=9,
            **_DEFAULTS,
        )
        self.assertEqual(len(plan["orders"]), 1)
        self.assertEqual(plan["orders"][0]["symbol"], "AAA")
        self.assertEqual(plan["orders_used_after"], 10)

    # --- Case 4: cash_buffer floor rejects ---------------------------- #
    def test_below_cash_buffer_rejects(self) -> None:
        # NAV=1_000_000 → cash_floor = 50_000 (5%).
        # Spendable above floor is only 5_000, which is < slice_value 16_000.
        plan = allocate_orders_serial(
            [{"symbol": "AAA", "price": 100.0}],
            nav=1_000_000.0,
            initial_cash=55_000.0,
            **_DEFAULTS,
        )
        self.assertEqual(plan["orders"], [])
        self.assertEqual(len(plan["skipped"]), 1)
        self.assertEqual(plan["skipped"][0]["reason"], "insufficient_cash")
        self.assertEqual(plan["orders_used_after"], 0)

    def test_partial_fill_does_not_break_buffer(self) -> None:
        # Edge case: spendable just barely covers slice_value, then a
        # second symbol whose theoretical notional would punch through
        # the buffer is rejected with ``insufficient_cash``.
        candidates = [
            {"symbol": "AAA", "price": 100.0},
            {"symbol": "BBB", "price": 100.0},
        ]
        # NAV = 1e6 → floor = 50_000; slice_value = 16_000.
        plan = allocate_orders_serial(
            candidates,
            nav=1_000_000.0,
            initial_cash=66_000.0,  # 16_000 above floor
            **_DEFAULTS,
        )
        self.assertEqual(len(plan["orders"]), 1)
        self.assertEqual(plan["orders"][0]["symbol"], "AAA")
        self.assertEqual(plan["skipped"][0]["symbol"], "BBB")
        self.assertEqual(plan["skipped"][0]["reason"], "insufficient_cash")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
