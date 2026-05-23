"""phase2.live — multi-symbol US daily live trading scaffolding.

This package houses the live-trading layer for phase2 (Futu OpenD SIM/REAL,
day-level rebalance). It MUST NOT modify or re-export any phase2.backtest or
phase2.strategy source. All cross-module wiring goes through small adapters
inside this package.

Layout:
- order_state.py : OrderIntent / OrderState reuse + light-weight phase2 wrappers
- risk.py        : copied & adapted from scripts/classic_multifactor/risk.py
- safety.py      : 6-switch validator for SIM/REAL launch
- broker.py      : LiveBroker protocol
- futu_broker.py : OpenSecTradeContext + OpenQuoteContext implementation
- live_adapter.py: futumd-compatible adapter that forwards to broker via gates
- guards.py      : 4-stage pre-trade gate pipeline
- runner.py      : DailyLiveRebalanceRunner (no vnpy CTA dependency)
"""

from __future__ import annotations

__all__: list[str] = []
