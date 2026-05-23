"""phase2 strategy self-optimization closed loop.

Hard isolation rules:
- This package MUST NOT import ``futu`` directly or transitively.
- This package MUST NOT import any module from ``phase2.live``.
- All trial executions go through the local backtest engine
  (``phase2.backtest.portfolio_backtest_engine``) with
  ``force_live_submit=True`` so the strategy stays on the pure-backtest
  branch and never reaches OpenD / SIM / REAL pathways.

The runtime guard below is a soft assertion: when any submodule of
``phase2.optimize`` is imported it verifies the futu SDK has not been
loaded into ``sys.modules`` *as a side effect* of this import chain.
The full integration check lives in
``phase2/strategy/tests/test_no_live_imports.py``.
"""

from __future__ import annotations

import sys

# NOTE: ``futu`` may already be imported elsewhere in the same Python
# process (for example, when running inside the same pytest session as
# live tests). The guard therefore only fails if this package itself
# pulls it in. We snapshot the state at import time and re-check it on
# every public entry point in :mod:`phase2.optimize.session`.
_FUTU_PRELOADED_AT_IMPORT: bool = "futu" in sys.modules


def assert_no_live_imports() -> None:
    """Fail loudly if forbidden modules slipped into ``sys.modules`` because
    of this package. Called by entry points before any trial executes.
    """

    forbidden = (
        "phase2.live",
        "phase2.live.futu_broker",
        "phase2.live.live_adapter",
        "phase2.live.runner",
    )
    leaked = [name for name in forbidden if name in sys.modules]
    if leaked:
        raise RuntimeError(
            "phase2.optimize must not import phase2.live.* "
            f"but found: {leaked!r}"
        )

    if not _FUTU_PRELOADED_AT_IMPORT and "futu" in sys.modules:
        raise RuntimeError(
            "phase2.optimize must not transitively import the 'futu' SDK"
        )


__all__ = ["assert_no_live_imports"]
