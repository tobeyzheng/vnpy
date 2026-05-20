"""Unit tests for ``phase2.strategy.pool_loader`` schema validation.

Run:  python -m pytest phase2/strategy/tests/test_pool_loader.py -v
Or:   python -m unittest phase2.strategy.tests.test_pool_loader -v
"""

from __future__ import annotations

import os
import sys
import textwrap
import tempfile
import unittest
from pathlib import Path

# Ensure repo root is importable when test is invoked directly.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from phase2.strategy.pool_loader import (  # noqa: E402
    POOL_HARD_CAP,
    PoolConfigError,
    load_pool_config,
)


def _write_tmp_yaml(content: str) -> str:
    fh = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    fh.write(textwrap.dedent(content))
    fh.flush()
    fh.close()
    return fh.name


class PoolLoaderTests(unittest.TestCase):
    """Four schema validation cases per task-item.md task 2."""

    def setUp(self) -> None:
        self._tmp_paths: list[str] = []

    def tearDown(self) -> None:
        for p in self._tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _yaml(self, content: str) -> str:
        path = _write_tmp_yaml(content)
        self._tmp_paths.append(path)
        return path

    # --- Case 1: happy path ------------------------------------------------ #
    def test_load_valid_pool_succeeds(self) -> None:
        path = self._yaml(
            """
            version: 1
            currency: USD
            max_pool_size: 20
            defaults:
              liquidity_min_adv60_usd: 50000000
              price_min: 5.0
              price_max: 800.0
              atr_pct_max: 0.08
              earnings_freeze_days: 2
            symbols:
              - {symbol: AAPL, market: US, market_cap_bucket: mega, sector: Technology}
              - {symbol: NVDA, market: US, market_cap_bucket: mega, sector: Technology}
            """
        )
        cfg = load_pool_config(path)
        self.assertEqual(cfg.currency, "USD")
        self.assertEqual(cfg.max_pool_size, 20)
        self.assertEqual(len(cfg.symbols), 2)
        self.assertEqual(cfg.symbol_list(), ["AAPL", "NVDA"])
        self.assertEqual(cfg.fq_symbol_list(), ["US.AAPL", "US.NVDA"])

    # --- Case 2: pool size exceeds hard cap (>20) -------------------------- #
    def test_pool_size_above_hard_cap_rejected(self) -> None:
        rows = "\n".join(
            f"  - {{symbol: SYM{i:02d}, market: US, market_cap_bucket: large, sector: Technology}}"
            for i in range(POOL_HARD_CAP + 1)  # 21 rows
        )
        # Build YAML manually to keep top-level keys at column 0 (no dedent surprises).
        path = self._yaml(
            "version: 1\n"
            "currency: USD\n"
            "max_pool_size: 20\n"
            "symbols:\n"
            f"{rows}\n"
        )
        with self.assertRaises(PoolConfigError) as ctx:
            load_pool_config(path)
        self.assertIn("hard cap", str(ctx.exception))

    # --- Case 3: non-USD currency rejected --------------------------------- #
    def test_non_usd_currency_rejected(self) -> None:
        path = self._yaml(
            """
            version: 1
            currency: HKD
            max_pool_size: 20
            symbols:
              - {symbol: AAPL, market: US, market_cap_bucket: mega, sector: Technology}
            """
        )
        with self.assertRaises(PoolConfigError) as ctx:
            load_pool_config(path)
        self.assertIn("USD", str(ctx.exception))

    # --- Case 4: missing required field rejected --------------------------- #
    def test_missing_required_field_rejected(self) -> None:
        path = self._yaml(
            """
            version: 1
            currency: USD
            max_pool_size: 20
            symbols:
              - {symbol: AAPL, market: US, market_cap_bucket: mega}
            """
        )
        with self.assertRaises(PoolConfigError) as ctx:
            load_pool_config(path)
        msg = str(ctx.exception)
        self.assertIn("missing required fields", msg)
        self.assertIn("sector", msg)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
