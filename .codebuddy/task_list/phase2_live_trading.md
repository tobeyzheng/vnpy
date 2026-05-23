# Phase-② live — 任务进度（权威）

> 进度判定以本文件为准；`.codebuddy/plan/phase2_live_trading/` 仅维护背景、需求与设计。

## 当前阶段

T1–T10 全部完成；新增 132 项单测全部通过，phase2 现有 75 项回归未受影响（`pytest phase2/` 合计 215/215，1.06s）；dry_run smoke 已跑通产物落盘于 `state/runs/phase2_live/dry_run/smoke_dry_run_t10/`。等待用户「确认提交推送」。

## 任务表

| ID | 状态 | 描述 | 关键产物 |
|----|------|------|----------|
| T1 | 已完成 | phase2/live/ 包骨架 + OrderIntent/OrderState/OrderStateStoreExt（22 单测） | phase2/live/__init__.py、phase2/live/order_state.py |
| T2 | 已完成 | classic_multifactor/risk.py copy → phase2/live/risk.py（去 classic_multifactor 依赖；18 单测） | phase2/live/risk.py |
| T3 | 已完成 | 6 开关安全模块（VNPY_LIVE_*/FUTU_TRADE_PASSWORD/--futu-env/--live-submit；28 单测） | phase2/live/safety.py |
| T4 | 已完成 | LiveBroker 协议 + FutuBroker（OpenSecTradeContext + OpenQuoteContext；15 单测） | phase2/live/broker.py、phase2/live/futu_broker.py |
| T5 | 已完成 | LivePortfolioRuntime + load_live_futumd_strategy（与回测 adapter 同接口；18 单测） | phase2/live/live_adapter.py |
| T6 | 已完成 | 4 级 pre-trade gate 编排（幂等 / 对账 / 单标风控 / 组合风控；events.jsonl + 状态机；23 单测） | phase2/live/guards.py |
| T7 | 已完成 | DailyLiveRebalanceRunner（注入 clock/sleep；REAL 强制 auto_cancel_on_eod；12 单测） | phase2/live/runner.py |
| T8 | 已完成 | CLI 入口（19 个 §8.1 必需参数；产物三态分目录；--help lazy；8 单测） | phase2/runners/run_phase2_live_daily.py |
| T9 | 已完成 | 文档同步：system_integration_guide.md + project_operation_log.md + 本 task_list | docs/、.codebuddy/ |
| T10 | 已完成 | 最终回归验证（pytest 215/215 + py_compile + dry_run smoke） | 控制台输出 + state/runs/phase2_live/dry_run/smoke_dry_run_t10/ |
| -- | -- | 第一次提交推送（需用户「确认提交推送」） | git commit / push |

## 单测结果

- phase2/live/tests/test_order_state.py：22 passed
- phase2/live/tests/test_risk.py：18 passed
- phase2/live/tests/test_safety.py：28 passed
- phase2/live/tests/test_broker.py：15 passed
- phase2/live/tests/test_live_adapter.py：18 passed
- phase2/live/tests/test_guards.py：23 passed
- phase2/live/tests/test_runner.py：12 passed
- phase2/live/tests/test_run_phase2_live_daily.py：8 passed
- phase2/live/tests/ 合计：132 passed
- phase2/strategy/tests/ + phase2/backtest/tests/ 既有：83 passed（零侵入回归）
- 总计：215/215（1.06s）
- dry_run smoke：`state/runs/phase2_live/dry_run/smoke_dry_run_t10/daily_report.json` 输出 pool=12、env=dry_run、intents=0、fills=0。intent=0 是在单日、只读 60 根日 K 上真实策略 5 项入场条件未同时命中的预期行为（全链路走通、安全开关放行、产物落盘、无异常）。

## 边界确认

- `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` 零改动；runner 内仅对**加载到的 strategy 模块**单边赋值 `LIVE_SUBMIT = bool(--live-submit)`。
- `phase2/backtest/*` 与 `phase2/strategy/*` 全部源码零改动。
- `scripts/classic_multifactor/*` 与 `tmp/*` 零改动。
- `phase2/live/*` 不 import `scripts.classic_multifactor.*`（risk.py 已通过专项 import 守门测试）。
- dry_run 默认不连 OpenD、不下任何 SIM/REAL 单；行情来自 vnpy 数据库。
- futu_sim / futu_real 必须**同时**满足 6 开关全置 + CLI `--futu-env` + CLI `--live-submit`，缺任一 stderr `BLOCKED` + 退出码 2。
- REAL 模式即使 `--no-auto-cancel-on-eod` 也强制收盘前撤单。
- 仅天级别 rebalance；分钟 / Tick 级需要新开 plan 加 `MinuteTradeGuard` 限频 gate。

## 产物三态分目录

每次运行落地于：

```
state/runs/phase2_live/{dry_run|futu_sim|futu_real}/<run_id>/
    daily_report.json
    events.jsonl
    orders/<request_id>.json
    positions_snapshot.csv
    reconcile/<ts>.json   # 由 runner 在 mid-cycle 写入
```

`<run_id>` 默认 `<execution_env>_<UTC timestamp>`，可通过 `--run-id` 覆盖。
