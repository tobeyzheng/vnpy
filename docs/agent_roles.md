# Multi-Agent Roles

## 0. Architect / Integrator Agent
Responsibilities:
- define project boundaries
- own roadmap and architecture
- review and integrate outputs from specialized agents
- maintain consistency of interfaces, config schema, and repository structure

Deliverables:
- architecture docs
- roadmap
- module contracts
- integration reviews

## 1. Strategy Research Agent
Responsibilities:
- design multi-strategy framework
- define factor groups and signal families
- design resonance rules and confidence scoring
- classify market-specific signal differences

Deliverables:
- strategy framework doc
- scoring rules
- candidate ranking logic

## 2. Data Engineering Agent
Responsibilities:
- unify data inputs across A/HK/US
- build symbol mapping and cache strategy
- normalize data for research, reporting, and execution

Deliverables:
- datahub adapters
- market config
- storage/cache contracts

## 3. Watchlist & Reporting Agent
Responsibilities:
- manage fixed + dynamic watchlists
- manage upgrade/downgrade/removal lifecycle
- generate premarket/midday/recap reports

Deliverables:
- watchlist manager
- candidate status manager
- report generator

## 4. Backtest & Evaluation Agent
Responsibilities:
- compare single-strategy and multi-strategy systems
- produce backtest metrics and benchmark comparisons
- evaluate false positives, drawdown, hit rate, turnover, concentration

Deliverables:
- experiment configs
- evaluation utilities
- benchmark reports

## 5. vnpy Execution Agent
Responsibilities:
- connect decision outputs to vnpy execution layer
- support paper trading first
- support signal->order translation and account sync

Deliverables:
- execution adapters
- portfolio engine integration
- paper/live switching scaffold

## 6. Risk Control Agent
Responsibilities:
- define position/risk constraints
- enforce pre-trade checks
- define market-event risk controls and suspension logic

Deliverables:
- risk rules
- risk config
- trade guards
