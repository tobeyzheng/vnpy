# Phase-② 多标本地回测 — 任务进度（权威）

> 进度判定以本文件为准；`plan/phase2_multi_backtest/README.md` 仅维护背景与设计。

## 当前阶段

无副作用代码 + 单测 + 文档已完成（T1–T11）。等待用户确认是否进入 T12（12 标真回测 smoke）。

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
| T8 | 已完成 | 引擎端到端单测（6 项，pass） | phase2/strategy/tests/test_portfolio_backtest_engine.py |
| T9 | 已完成 | 文档同步：system_integration_guide.md + project_operation_log.md | docs/ |
| T10 | 已完成 | 计划+task_list 落档 | .codebuddy/ |
| T11 | 已完成 | 全套单测回归（73 项全绿）+ py_compile + CLI --help | 控制台输出 |
| -- | -- | 第一次提交推送（需用户「确认提交推送」） | git commit / push |
| T12 | 等待用户确认 | 12 标 1 年真回测 smoke（需用户「确认执行」） | state/runs/phase2_multi_backtest/<run_id>/ |
| -- | -- | 第二次提交推送（需用户「确认提交推送」） | git commit / push |

## 单测结果

- phase2/strategy/tests/test_futumd_strategy_adapter.py：17 passed
- phase2/strategy/tests/test_portfolio_backtest_engine.py：6 passed
- phase2/strategy/tests/ 全套：73 passed

## 边界确认

- phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py 零改动
- tmp/ 代码目录零改动（仅 tmp/data/cache_index.json 因 vnpy 数据库读取被动更新，不进本计划 commit）
- 不连 OpenD / Futu / 远端服务
- LIVE_SUBMIT 保持 False
