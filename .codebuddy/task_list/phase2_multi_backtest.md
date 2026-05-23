# Phase-② 多标本地回测 — 任务进度（权威）

> 进度判定以本文件为准；`plan/phase2_multi_backtest/README.md` 仅维护背景与设计。

## 当前阶段

T12 完成；引擎默认 force_live_submit=True 已上线（futumd 源码零改动）。等待用户确认第二次提交推送。

## 任务表

| ID | 状态 | 描述 | 关键产物 |
|----|------|------|----------|
| T1 | 已完成 | 新建 phase2/backtest/ 包骨架 | phase2/backtest/__init__.py |
| T2 | 已完成 | 实现 PortfolioRuntime（按 symbol 分桶） | phase2/backtest/futumd_strategy_adapter.py |
| T3 | 已完成 | 实现 build_futumd_namespace（DSL 全部按 symbol 路由） | 同上 |
| T4 | 已完成 | 实现 load_futumd_strategy（importlib 注入命名空间后 exec） | 同上 |
| T5 | 已完成 | 实现 PortfolioBacktestEngine（日期 union + 次日开盘撮合 + 4 份报告） | phase2/backtest/portfolio_backtest_engine.py |
| T6 | 已完成 | CLI 入口 run_phase2_multi_backtest.py | phase2/runners/run_phase2_multi_backtest.py |
| T7 | 已完成 | adapter 单测（17 项，pass） | phase2/strategy/tests/test_futumd_strategy_adapter.py |
| T8 | 已完成 | 引擎端到端单测（6 项 + force_live_submit 对照 2 项 = 8 项，pass） | phase2/strategy/tests/test_portfolio_backtest_engine.py |
| T9 | 已完成 | 文档同步：system_integration_guide.md + project_operation_log.md | docs/ |
| T10 | 已完成 | 计划+task_list 落档 | .codebuddy/ |
| T11 | 已完成 | 全套单测回归（75 项全绿）+ py_compile + CLI --help | 控制台输出 |
| -- | -- | 第一次提交推送（已完成 commit 0678fa2） | git commit / push |
| T12 | 已完成 | 12 标 1 年真回测 smoke（trade_count=23, total_return=+1.34%, max_dd=3.59%） | state/runs/phase2_multi_backtest/smoke_2025_2026_v2/ |
| T13 | 已完成 | 引擎引入 force_live_submit 开关（默认 True，回测才能看到 place_limit intent；可通过 --respect-live-submit 关闭） | phase2/backtest/portfolio_backtest_engine.py + phase2/runners/run_phase2_multi_backtest.py |
| -- | -- | 第二次提交推送（需用户「确认提交推送」） | git commit / push |

## 单测结果

- phase2/strategy/tests/test_futumd_strategy_adapter.py：17 passed
- phase2/strategy/tests/test_portfolio_backtest_engine.py：8 passed（含 force_live_submit 对照）
- phase2/strategy/tests/ 全套：75 passed

## smoke 结果（state/runs/phase2_multi_backtest/smoke_2025_2026_v2/）

- 池：12 标（AAPL/MSFT/NVDA/GOOGL/META/AMZN/TSLA/AVGO/AMD/JPM/XOM/UNH）
- 区间：2025-05-21 → 2026-05-20（251 个交易日）
- trade_count：23（7 只标的有交易：AAPL/AVGO/JPM/META/MSFT/TSLA/UNH）
- final_nav：1,013,361.67（total_return +1.3362%，annualised +1.3415%）
- max_drawdown：3.5868%
- win/loss：4/8
- 末日全部 0 仓（多标 round-trip 闭合）

## 边界确认

- phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py 零改动（迁移到 Futu 平台后 LIVE_SUBMIT 仍 False）
- tmp/ 代码目录零改动（仅 tmp/data/cache_index.json 因 vnpy 数据库读取被动更新，不进本计划 commit）
- 不连 OpenD / Futu / 远端服务（adapter 的 place_limit 仅写内存队列）
- 引擎 force_live_submit=True 仅在本地回测语境下覆盖；strategy.LIVE_SUBMIT 在 Futu 平台部署时仍 ships False
