# 04 — Futu 平台性能基线（阶段 ② 前置验证骨架）

> 本文档为研究/工程性文档，**不含可执行代码**；仅作为 [run_futu_perf_baseline.py](/projects/vnpy/phase2/runners/run_futu_perf_baseline.py) 的产物落点与判定基线。任何真实执行须先在会话中获得用户明确确认（项目规则 2）。

## 1. 目的

在阶段 ② 大规模落地前，先验证 Futu 平台在 1 / 10 / 30 标的三档下的：

- `handle_data` 单次执行平均耗时 / p95 耗时
- 回测引擎总耗时
- 串行下单延迟

并据此判定阶段 ② 池上限是否需要从 20 降到 10（参见 [03 §1.2](./03_strategy_design.md) / 阶段 ② [requirements.md §需求 5](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant_phase2/requirements.md)）。

## 2. 测试矩阵（计划）

| 档位 | 标的数 N | 时间窗 | 回测周期 | 资金 | 备注 |
|------|---------|-------|---------|------|------|
| T1   | 1       | 30 个交易日 | 日 K | 占位 | 单标基线，对照组 |
| T10  | 10      | 30 个交易日 | 日 K | 占位 | 阶段 ② 池规模下限 |
| T30  | 30      | 30 个交易日 | 日 K | 占位 | 平台 50 上限的 60%，覆盖阶段 ② 池上限 + 余量 |

> 选样口径：从 `phase2/strategy/config/pool_config.yaml` 中按 sector 均匀抽取（不超过单 sector 50%）。具体 symbol 列表在执行前按池配置最终落定。

## 3. 待填实测（用户确认后回填）

> 真实执行未发生；下表占位仅用于结构对照，不得作为决策依据。

| 档位 | handle_data avg (ms) | handle_data p95 (ms) | engine total (s) | serial order delay (ms) | 备注 |
|------|----------------------|----------------------|------------------|------------------------|------|
| T1   | —                    | —                    | —                | —                      | 待用户确认执行后回填 |
| T10  | —                    | —                    | —                | —                      | 待用户确认执行后回填 |
| T30  | —                    | —                    | —                | —                      | 待用户确认执行后回填 |

## 4. 判定规则（与脚本 exit code 对齐）

- **达标**：T30 档 `handle_data` p95 < 5 秒 → 阶段 ② 池上限保持 20；脚本退出码 0。
- **降级**：T30 档 `handle_data` p95 ≥ 5 秒 → 阶段 ② 池上限降到 10，并在 [docs/project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) 记录降级；脚本退出码 4。
- **执行未授权**：脚本带 `--execute` 但缺 `--confirm` 或缺会话内确认 → 直接拒绝；退出码 3。
- **参数错误**：tiers 解析失败、tier 超过 50 → 退出码 2。
- **dry-run**：不连 Futu、不下单、不写产物（除非显式 `--output`）；退出码 0。

## 5. 执行边界（项目规则 2 对齐）

| 动作 | 是否需要会话确认 | 当前状态 |
|------|------------------|----------|
| 仅 dry-run（默认） | 否 | 已自测通过 |
| `--execute` 真实跑 Futu 回测 | 是 | 待用户确认 |
| 改写 `state/runs/` / 任何账户态 | 不在本脚本范围 | 不允许 |
| SIM/REAL 下单 | 不在本脚本范围 | 不允许 |

## 6. 阅读路径反向引用

- ⬅️ [00 入口 — 阅读路径](./00_index.md)
- 上游：[03 多标的策略方案](./03_strategy_design.md)
- 下游：[05 SIM 灰度准入清单](./05_sim_gate_checklist.md)（本计划任务 8 落地后生效）

