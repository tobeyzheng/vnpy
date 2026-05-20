---
plan: us_multi_symbol_quant_phase2_v2
last_updated: 2026-05-20
focus: 全部完成；等待用户确认提交推送
---

# Task list — us_multi_symbol_quant_phase2_v2

> 本文件是计划完成进度的权威记录（项目规则 3）。所有状态以本文件为准。

## 任务清单

| # | 任务 | 状态 | 主要产物 |
|---|------|------|----------|
| T1 | futumd 兼容单文件策略（零外部 import、严格对齐 NVDA 接口） | ✅ done | `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` |
| T2 | 池更新流水线（候选池 yaml + run_pool_update + 3 道安全闸 + 审计 jsonl） | ✅ done | `phase2/runners/run_pool_update.py`、`phase2/strategy/config/pool_universe.yaml`、`phase2/strategy/config/pool_metrics_snapshot.json` |
| T3 | 单元测试（迁移版接口契约 6 + 池更新 5） | ✅ done | `phase2/strategy/tests/test_futumd_strategy.py`、`phase2/strategy/tests/test_pool_updater.py`，phase2 集 50/50 全过 |
| T4 | 真实回测 runbook + 文档同步 | ✅ done | `docs/research/us_multi_symbol_quant/06_real_backtest_runbook.md`、`docs/system_integration_guide.md`、`docs/project_operation_log.md` |
| T5 | 进度同步 + 全量验证 + 报 push 影响范围等用户确认 | 🟡 待用户确认 | 本文件 + 验证输出已就绪 |

## 当前执行焦点

- T1~T4 已完成；T5 等用户回复 `确认提交推送`。
- 验证摘要：
  - py_compile：futumd 策略 + run_pool_update 全过
  - `--check`：futumd 策略自检 OK，池=12，LIVE_SUBMIT=False
  - dry-run：池更新脚本可干净打印 diff（在合理 max_change_ratio 下退 0）
  - 测试：phase2/strategy/tests/ 50 passed
  - 敏感词扫描：不含真实账号 / 金额（默认占位 1,000,000 USD）

## 边界与红线

- `LIVE_SUBMIT` 出厂 False；阶段 ② v2 不允许翻 True。
- 迁移版策略禁止 `from phase2.*` / `from services.*`；测试强制 AST 扫描。
- 池更新脚本默认 dry-run；`--apply --confirm` 才写盘。
- 真实回测 Futu 主轨手工执行；脚本不自动连 Futu。

