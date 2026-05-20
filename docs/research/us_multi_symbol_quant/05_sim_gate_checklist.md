---
title: 阶段② SIM 准入清单（5 项打勾）
plan: us_multi_symbol_quant_phase2
status: draft
last_updated: 2026-05-20
---

# 阶段② SIM 准入清单

> 本清单用于在 **从阶段② Dry-Run 升级到 Futu SIM** 时做准入把关。
> 当前计划 `us_multi_symbol_quant_phase2` 仅完成研究、配置、策略骨架与 dry-run runner，**禁止**直接在本计划下启动 SIM；必须新开一个独立 plan（建议命名 `us_multi_symbol_quant_phase3_sim`），并在新 plan 中完成 5 项打勾。

## 项目硬约束（红线）

1. **LIVE_SUBMIT 默认 False**：`phase2/strategy/us_multi_symbol_phase2_strategy.py` 中 `LIVE_SUBMIT = False` 是出厂默认值，本仓库任何 commit 不得改为 True。
2. **本 plan 禁止 SIM 启动**：本计划仅做研究 + 骨架 + dry-run；任何 SIM 连接、SIM 下单、SIM 调仓、SIM 平仓动作必须在独立 plan 中完成。
3. **REAL/live 始终禁止**：未经用户书面确认 + 项目硬开关 + 人工审批 + 对账 + 风控 + 订单幂等 6 项全部就绪，任何分支不得提交 REAL 订单。
4. **runner 默认 dry-run**：`run_phase2_backtest.py` / `run_phase2_reconcile.py` 默认 `--dry-run`；`--no-dry-run` 在本仓库分支下直接退出码 3，提示需新开 plan。

## 5 项准入打勾

> 所有项默认 ❌；必须在新 plan 内逐项完成并由用户书面确认后改为 ✅。

| # | 准入项 | 验证标准 | 验证产物 | 状态 |
|---|---|---|---|---|
| 1 | **平台性能基线** | 50 标的 1d 全量拉取 P95 ≤ 阶段①基线的 1.5 倍；订阅、行情、下单 API 在 100 笔 mock 调用内无超时 | `state/runs/<phase3_plan>/<run_id>/perf_baseline.json` + `docs/research/us_multi_symbol_quant/04_platform_performance_baseline.md` 数据回填 | ❌ |
| 2 | **池配置 & 过滤通过** | `pool_config.yaml` 通过 `pool_loader` schema 校验；4 类过滤（流动性 / 价格 / ATR% / 财报冻结）测试 16/16 通过；池更新日志在最近 5 个交易日均落盘 | `state/pool_updates/<date>.json`；`python3 -m unittest phase2.strategy.tests.test_pool_loader phase2.strategy.tests.test_pool_filters` 全绿 | ❌ |
| 3 | **风控规则 & 熔断状态** | 单标 4 条 + 组合 5 条规则单元测试 14/14 通过；熔断状态 JSON 重启可恢复；SIM 干跑出现一次模拟触发并写入 `portfolio_state.json` | `state/runs/<phase3_plan>/<run_id>/portfolio_state.json`（含 `breaker_triggered=true` 演练样本）；`python3 -m unittest phase2.strategy.tests.test_risk_rules` 全绿 | ❌ |
| 4 | **回测对账达标** | 5 项指标（return / max_dd / sharpe / vol / win_rate）失败比例 ≤ 20%；稳健性图已渲染（非占位） | `state/runs/<phase3_plan>/<run_id>/reconcile_report.json` (`verdict=PASS`) + `robustness.png`（真图） | ❌ |
| 5 | **SIM 演练完成** | 在独立 plan 下：连接 OpenD SIM；订阅 50 标的 1d；预算分配串行下 ≥ 5 笔 SIM 单；触发一次 hard_stop / trailing_tp 之一并平仓；对账误差 ≤ 1bp | `state/runs/<phase3_plan>/<sim_run_id>/orders.json`、`fills.json`、`reconcile_report.json` | ❌ |

## 升级流程

1. 新建 plan：`.codebuddy/plan/us_multi_symbol_quant_phase3_sim/`，含背景、需求、任务拆解、验收标准。
2. 同步建立 `.codebuddy/task_list/us_multi_symbol_quant_phase3_sim.md`。
3. 在新 plan 下逐项打勾本清单 1–5；任何一项不通过都不得启动 SIM 实盘连线。
4. 5 项全部 ✅ 后，由用户在对话中明确回复 `确认启动 SIM` 才能跑首次 SIM 命令。
5. 首次 SIM 命令执行前，AI 必须按项目规则 2 重申影响范围（连接 OpenD？是否提交 SIM 订单？预计落盘文件？）并等待二次确认。

## 关联文档

- 需求与方案：[`01_requirements.md`](./01_requirements.md)、[`02_strategy_design.md`](./02_strategy_design.md)
- 风险与回滚：[`03_risk_and_rollback.md`](./03_risk_and_rollback.md)
- 平台性能基线：[`04_platform_performance_baseline.md`](./04_platform_performance_baseline.md)
- 集成入口：[`docs/system_integration_guide.md`](../../system_integration_guide.md)（阶段② 章节）
- 操作日志：[`docs/project_operation_log.md`](../../project_operation_log.md)
