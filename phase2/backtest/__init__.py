# -*- coding: utf-8 -*-
"""Phase-② local multi-symbol backtest package.

This sub-package hosts a *self-contained* local backtest stack for the
phase-② futumd-compatible strategy
(`phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`).

Design boundary:
- Zero modifications to the futumd strategy file (it must remain
  uploadable to the Futu platform unchanged).
- Zero imports from `tmp/`; this package is self-contained so that it
  can be migrated together with the futumd strategy.
- No OpenD / Futu connection. No live order submission. The driver
  reads bars from the local vnpy database only.

Public API (kept small and stable):
- ``PortfolioRuntime``       — per-symbol bucketed OHLCV / cash / pos state.
- ``build_futumd_namespace`` — returns a dict that injects the futumd
                               DSL names (bar_close / cash / place_limit ...)
                               so a plain `exec()` of the strategy file
                               binds them as already defined.
- ``load_futumd_strategy``   — dynamic loader that exec's the strategy
                               file with the DSL namespace and returns
                               its ``Strategy`` class.
- ``PortfolioBacktestEngine``— date-union driver with next-bar-open fills.
"""

from .futumd_strategy_adapter import (  # noqa: F401
    PortfolioRuntime,
    build_futumd_namespace,
    load_futumd_strategy,
)
from .portfolio_backtest_engine import (  # noqa: F401
    PortfolioBacktestEngine,
    BacktestResult,
)
