---
plan: us_multi_symbol_quant_phase2
last_updated: 2026-05-20
focus: 阶段② dry-run 全部完成；等待用户确认后做收口提交推送
---

# Task list — us_multi_symbol_quant_phase2

> 本文件是计划完成进度的权威记录（项目规则 3）。所有状态以本文件为准。

## 任务清单

| # | 任务 | 状态 | 主要产物 |
|---|------|------|----------|
| 1 | 平台 50 标的性能基线（runner + 文档骨架 + 链入 00_index） | ✅ done | `phase2/runners/run_futu_perf_baseline.py`、`docs/research/us_multi_symbol_quant/04_platform_performance_baseline.md` |
| 2 | 池配置 + 加载器 + schema 校验 + 4 用例 | ✅ done | `phase2/strategy/config/pool_config.yaml`、`phase2/strategy/pool_loader.py`、`phase2/strategy/tests/test_pool_loader.py` |
| 3 | 4 类过滤函数（流动性 / 价格 / ATR% / 财报冻结）+ 池更新日志 + 8 用例 | ✅ done | `phase2/strategy/pool_loader.py`、`phase2/strategy/tests/test_pool_filters.py` |
| 4 | 多标策略骨架（4 因子 + 入场 5 项 + 出场链 + 冷却） | ✅ done | `phase2/strategy/us_multi_symbol_phase2_strategy.py` |
| 5 | 预算分配 + 串行下单 + cash_buffer + max_orders_per_day + 4 用例 | ✅ done | 同上策略文件 + `phase2/strategy/tests/test_budget_allocator.py` |
| 6 | 单标 4 + 组合 5 风控规则 + 熔断状态落盘 / 重启恢复 + 14 用例 | ✅ done | `phase2/strategy/portfolio_risk.py`、`phase2/strategy/tests/test_risk_rules.py` |
| 7 | 回测 runner + 对账 runner（5 项指标 / >20% 失败退出 / 稳健性占位图）默认 `--dry-run` | ✅ done | `phase2/runners/run_phase2_backtest.py`、`phase2/runners/run_phase2_reconcile.py` |
| 8 | SIM 准入清单（5 项打勾）+ 在策略与清单中标注 SIM 启动需独立 plan | ✅ done | `docs/research/us_multi_symbol_quant/05_sim_gate_checklist.md`、策略首注释 HARD GATE 段、`00_index.md` 阅读路径 |
| 9 | 同步 system_integration_guide / project_operation_log / 本 task_list；敏感词扫描 = 0 | ✅ done | `docs/system_integration_guide.md` 阶段② 章节、`docs/project_operation_log.md` 2026-05-20 条目、本文件 |
| 10 | 阶段② 收口：先发 push 影响范围预告等用户确认，再 `git add -A` + 中文 commit + `git push` | ⏳ pending | 等用户确认后执行 |

## 当前执行焦点

- 任务 1–9 已完成，自动化测试 39/39 通过；3 条 dry-run smoke（回测 / 对账 PASS / 对账 FAIL）退出码符合预期。
- 任务 10 等待用户在影响范围预告后明确回复 `确认提交推送` 才能 `git add -A` + commit + push。

## 边界与红线

- `LIVE_SUBMIT` 出厂值 `False`，本仓库分支不得改 `True`。
- 本 plan 严格 dry-run；从 Dry-Run 升级到 Futu SIM 必须新开独立 plan（建议 `us_multi_symbol_quant_phase3_sim`），并按 `docs/research/us_multi_symbol_quant/05_sim_gate_checklist.md` 5 项逐项打勾。
- runner `--no-dry-run` 在本仓库分支会直接退出码 3，提示需新开 plan。
- REAL/live 始终禁止；受 `--live-submit` + `VNPY_LIVE_*` + 硬开关 + 人工审批 + 对账 + 风控 + 订单幂等 6 项共同约束，与阶段①一致。
