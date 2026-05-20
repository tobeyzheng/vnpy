---
plan: us_multi_symbol_quant_phase2_v2
parent_plan: us_multi_symbol_quant_phase2
created: 2026-05-20
---

# 任务拆解 — us_multi_symbol_quant_phase2_v2

## 任务清单（按依赖顺序）

### T1 — futumd 兼容多标策略（单文件、零外部依赖）
- 新增 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`
- 严格对齐 NVDA `Strategy` 五件套 + `_enter_position`/`_exit_position`
- 所有指标 / 风控 / 预算逻辑收为 `Strategy._xxx` 私有方法
- 内置常量池（`AAPL`/`MSFT`/`NVDA`/`GOOGL`/`META`/`AMZN`/`TSLA`/`AVGO`/
  `AMD`/`JPM`/`XOM`/`UNH`，与 `pool_config.yaml` 对齐），≤ 20 上限
- 4 因子 + 5 入场条件 + 4 出场链 + 多标预算 + 多标冷却（全部内联）
- 提供 `_self_check()` CLI（`--check`）

### T2 — 池更新流水线
- 新增 `phase2/strategy/config/pool_universe.yaml`（候选大池占位 ≤ 30）
- 新增 `phase2/runners/run_pool_update.py`
  - 默认 dry-run；`--apply --confirm` 才写盘
  - 安全闸：变更 >30% / 超 max_pool_size / sector >40% → 非零退出
  - 写 `state/pool_update_log.jsonl` + 复用 `append_pool_change_log`
- 数据来源：本地 `tmp/data/` 离线 K 线（缺则报错而非编造）

### T3 — 单元测试
- `phase2/strategy/tests/test_futumd_strategy.py`
  - AST 扫描：迁移版文件 0 个 `from phase2.*` / `from services.*`
  - 接口契约：必有 5 件套 + 私有方法
  - 池规模 ≤ 20、`LIVE_SUBMIT == False`
- `phase2/strategy/tests/test_pool_updater.py`
  - dry-run 不落盘
  - `--apply --confirm` 写盘 + 写 jsonl
  - 变更 >30% / sector >40% / size 超限三类闸用例

### T4 — 真实回测 runbook + 文档同步
- 新增 `docs/research/us_multi_symbol_quant/06_real_backtest_runbook.md`
- 改 `docs/system_integration_guide.md` 追加 "futumd 投放路径" + "真实
  回测命令" 两节
- 改 `docs/project_operation_log.md` 追加 2026-05-20 v2 条目

### T5 — 进度同步 & 验证 & 等用户确认推送
- 同步本目录与 `.codebuddy/task_list/us_multi_symbol_quant_phase2_v2.md`
- 跑 py_compile + pytest + `--check` + 池更新 dry-run
- 报 push 影响范围，等用户 `确认提交推送`

## 依赖关系

```
T1  ──┐
T2  ──┼──> T3 ──> T4 ──> T5
      │
T1 ───┘
```

## 不做

- 不动 `phase2/strategy/us_multi_symbol_phase2_strategy.py`
- 不动现有 39 个测试
- 不动 `services/backtest`
- 不联网（行情 / Futu OpenD / 远端 API）

