# Multi-Agent Architecture for Personal Multi-Strategy Stock Selection & Quant Trading System

## Goal
Build a personal-use multi-strategy stock selection, watchlist tracking, and quantitative trading system on top of the vnpy project.

The system should support:
- A-share / Hong Kong / US markets
- fixed watchlists + dynamic candidate pools
- multi-strategy resonance scoring
- pre-market / midday / post-market reporting
- backtesting and evaluation
- gradual evolution from research system to execution system

## Layered Design

### 1. Research Layer
Responsible for idea generation and candidate discovery.

Core responsibilities:
- factor signals
- technical/fundamental/event/risk inputs
- candidate screening
- multi-strategy resonance scoring
- market-specific ranking

### 2. Decision Layer
Responsible for converting research outputs into actionable suggestions.

Core responsibilities:
- Top5 candidate generation
- watchlist promotion / downgrade / removal
- confidence scoring
- action suggestions: observe / buy-on-pullback / trial position / hold / reduce / avoid
- market regime judgment: offensive / balanced / defensive

### 3. Execution Layer
Responsible for simulated/live trading integration with vnpy.

Core responsibilities:
- signal routing
- portfolio constraints
- pre-trade checks
- order generation and execution adapters
- account synchronization

## Core Modules

### services/datahub
Unified market data access and normalization.

### services/candidate_engine
Candidate discovery and ranking.

### services/watchlist_engine
Fixed watchlist + dynamic watchlist lifecycle management.

### services/scoring_engine
Multi-strategy scoring and resonance framework.

### services/reporting
Premarket / midday / recap report generation.

### execution/*
Execution pipeline and account/risk integration.

### risk/*
Portfolio risk rules, trade filters, drawdown controls.

### backtests/*
Backtest experiments, benchmark comparisons, evaluation reports.

## Output Principles
- human-readable explanations first
- machine-structured state second
- market-specific logic allowed, but common interfaces must be unified
- support gradual rollout: report-only -> paper trading -> semi-auto -> live

## State Management
Runtime state should be stored outside strategy code.

Recommended state categories:
- `state/watchlists/`
- `state/candidates/`
- `state/runs/`

## Initial Milestones
1. define role boundaries and shared interfaces
2. build watchlist + candidate + scoring skeleton
3. build premarket/midday/recap reporting pipeline
4. add backtest/evaluation layer
5. connect vnpy execution in paper mode
6. add portfolio/risk controls before live trading
