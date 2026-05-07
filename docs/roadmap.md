# Roadmap

## Phase 1 - Research & Tracking System
Goal: deliver a useful personal multi-strategy tracking system before building live execution.

Scope:
- fixed watchlists
- dynamic watchlist adjustments
- Top5 candidate selection by market
- confidence scoring
- premarket / midday / recap reports
- market regime classification

Success criteria:
- daily outputs are readable, stable, and actionable
- watchlist lifecycle is explainable
- cross-market comparisons are available

## Phase 2 - Evaluation & Backtest
Goal: validate whether multi-strategy resonance adds value.

Scope:
- backtest candidate selection quality
- compare strategies and combined model
- evaluate hit rate, return, drawdown, turnover, false positives
- identify market-specific best practices

Success criteria:
- reproducible evaluation reports
- validated scoring framework
- clear elimination of weak signals

## Phase 3 - Paper Trading with vnpy
Goal: connect decision layer to execution layer safely.

Scope:
- signal routing
- order simulation
- account synchronization
- pre-trade risk controls

Success criteria:
- no hidden auto-trading behavior
- all orders are explainable and auditable
- paper mode stable

## Phase 4 - Semi-Automated Personal Trading
Goal: limited personal-use deployment with strong guardrails.

Scope:
- manual approval checkpoints
- configurable capital/risk limits
- execution monitoring
- post-trade evaluation

Success criteria:
- safe execution with limited blast radius
- clear rollback path
- portfolio/risk limits enforced
