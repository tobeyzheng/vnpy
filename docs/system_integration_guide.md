# 量化交易系统接入梳理

## 1. 当前系统定位

本项目当前定位为个人港股/美股多策略选股跟踪与量化交易系统，核心边界如下：

- `scripts/`：只保留任务入口和市场配置。
- `services/`：承载候选池、策略、风控、订单状态、对账、健康检查等业务逻辑。
- `execution/`：承接 paper/vn.py/Futu 执行桥接，但默认不自动实盘。
- `state/`：保存候选、观察池、报告、订单状态、对账结果和运行快照。
- `vn.py` / `vnpy_futu`：作为行情、账户、订单、成交、回测和网关底座。

当前安全原则：

- `live_submit_enabled = False`
- `VnpyExecutor` 仅支持 `paper/sim` dry-run 状态落盘。
- 默认不调用 `MainEngine.send_order()`。
- 默认不调用 `FutuGateway.send_order()`。
- Futu SIM submit 脚本提交前必须通过 reconciliation。

## 2. 交易主入口文件

### 港股交易入口

- 盘中主任务：`scripts/run_hk_sim_task.py`
  - 已改为调用 `services/trading_pipeline/sim_task.py`。
  - 已启用 `reconciliation_required=True`。
- 盘后收盘：`scripts/run_hk_sim_close.py`
- 盘中盯市/估值：`scripts/run_hk_sim_mark.py`
- 港股 Knot 刷新：`scripts/run_knot_agent_hk_refresh.py`
- 港股盘中 Knot 决策：`scripts/run_intraday_knot_decision.py`
- Futu SIM 买入提交验证：`scripts/demo_futu_sim_submit.py`
  - 已接入 `ReconciliationGuard`。
- Futu SIM 卖出提交验证：`scripts/demo_futu_sim_sell_submit.py`
  - 已接入 `ReconciliationGuard` 和可卖数量约束。

### 美股交易入口

- 盘中主任务：`scripts/run_us_sim_task.py`
  - 已改为调用 `services/trading_pipeline/sim_task.py`。
- 盘后收盘：`scripts/run_us_sim_close.py`

### 日常运维入口

- 健康检查：`scripts/run_healthcheck.py`
- 组合简报 / daily brief：`scripts/run_portfolio_brief.py`
- 多市场简报：`scripts/run_multi_market_brief.py`
- 日常流水线：`scripts/run_daily_pipeline.py`
  - 支持 `--mode premarket`
  - 支持 `--mode midday`
  - 支持 `--mode recap`
  - 支持 `--mode healthcheck`
  - 支持 `--mode brief`

## 3. 统一交易 pipeline

### 主模块

- `services/trading_pipeline/sim_task.py`

核心类：

- `MarketSimTaskConfig`
- `MultiMarketSimTradingPipeline`

当前职责：

1. 按市场读取候选池。
2. 获取 Futu 行情快照。
3. 调用统一 `StrategyEngine` 生成策略评价。
4. 构造 `task_candidate_pool`。
5. 按分数排序并选出候选。
6. 执行本地 `SimAccount` 模拟买入。
7. 写出 `hk_sim_task_report.json` / `us_sim_task_report.json`。
8. 可选执行 reconciliation 阻断。

当前仍未抽象的部分：

- `run_hk_sim_close.py` / `run_us_sim_close.py` 仍是两个脚本，下一步应抽成统一 close pipeline。
- 本地 `SimTradingEngine` 成交尚未全部写入 `OrderStateStore`。

## 4. 候选池入口

### 文件

- 统一候选提供器：`services/strategy/candidate_provider.py`
- 静态候选文件：`state/runs/candidate_inputs.json`
- 动态候选文件：`state/runs/candidate_inputs.dynamic.json`
- 测试：`tests/test_candidate_provider.py`

### 当前逻辑

`UnifiedCandidateProvider.load(market)`：

1. 优先读取 dynamic 中对应 `market` 的候选。
2. 如果 dynamic 不含该 `market`，回退 static。
3. 统一执行 `normalize_symbol()`。

`UnifiedCandidateProvider.load()`：

1. dynamic 覆盖其已包含市场。
2. static 补齐 dynamic 未覆盖市场。
3. 避免 dynamic 只有港股时，美股候选被整体吞掉。

## 5. 策略系统入口

### 基础模块

- 多因子评分：`services/strategy/raw_score.py`
- 入场/离场 timing：`services/strategy/timing.py`
- 单标的风控：`services/strategy/risk_guard.py`
- 市场规则：`services/strategy/market_rules.py`
- 符号规范化：`services/strategy/symbols.py`

### 新增策略注册与统一策略引擎

- 策略注册：`services/strategy/registry.py`
- 策略引擎：`services/strategy/engine.py`
- 测试：`tests/test_strategy_engine.py`

核心类：

- `StrategyDefinition`
- `StrategyRegistry`
- `StrategyEngine`
- `StrategyEvaluation`

当前默认策略：

- `raw_score_timing_v1`

当前已接入：

- `services/trading_pipeline/sim_task.py`
- `services/backtest/vnpy_strategy_bridge.py`
- `services/backtest/portfolio_engine.py`

目标：让模拟、回测、未来实盘复用同一套策略计算，避免回测和交易逻辑分叉。

## 6. 订单协议与状态机

### 统一协议

- `services/common/trading_models.py`
- `services/common/__init__.py`

核心模型：

- `StrategySignal`
- `OrderIntent`
- `OrderState`
- `OrderStatus`
- `OrderSide`
- `Direction`

### 订单状态机

- 状态机：`services/trade_state/state_machine.py`
- 状态存储：`services/trade_state/storage.py`
- 包导出：`services/trade_state/__init__.py`
- 测试：`tests/test_order_state_machine.py`

核心类：

- `OrderStateMachine`
- `OrderStateStore`
- `InvalidOrderTransition`

支持状态流：

```text
created
-> validated
-> risk_checked
-> approval_required / approved
-> submitting
-> submitted
-> partial_filled / filled / cancelled / rejected / failed
-> reconciled
```

当前接入：

- `VnpyExecutor` paper dry-run 会写入 `state/runs/orders/<request_id>.json`。
- `HealthcheckService` 会统计订单状态数量。

## 7. vn.py / Futu 执行桥接

### 现有 draft 桥接

- `execution/paper_bridge/`
- `execution/futu_bridge/`
- `execution/vnpy_bridge/bridge.py`

说明：

- `VnpySignalBridge` 只生成 `VnpyOrderDraft`。
- `FutuPaperBridge` 只生成 Futu draft。
- draft 本身不提交订单。

### 新增 VnpyExecutor

- `execution/vnpy_bridge/executor.py`
- `execution/vnpy_bridge/__init__.py`

核心类：

- `VnpyExecutor`
- `VnpyGatewayEventBridge`

当前行为：

- 支持 `mode="paper"`。
- 支持 `mode="sim"`，但仍为 dry-run scaffold。
- 不调用 `MainEngine.send_order()`。
- 不调用 `FutuGateway.send_order()`。
- 不调用 `FutuSimTradeClient.submit_limit_order()`。
- 对 `WATCH` 类信号不做执行映射，避免误买入。

### vn.py 事件记录

- `execution/vnpy_bridge/event_recorder.py`

核心类：

- `VnpyEventRecorder`

可注册到 vn.py `EventEngine`，记录：

- `EVENT_ORDER`
- `EVENT_TRADE`
- `EVENT_POSITION`
- `EVENT_ACCOUNT`

输出：

- `state/runs/vnpy_gateway_events_YYYYMMDD.jsonl`

当前没有直接修改 `vnpy_futu/futu_gateway.py`，避免影响网关原生行为。

## 8. reconciliation 与执行阻断

### 对账脚本

- `scripts/reconcile_futu_sim_positions.py`
- 输出：`state/runs/futu_sim_position_reconcile.json`

### 对账守卫

- `services/execution_guard/reconciliation.py`
- 测试：`tests/test_reconciliation_guard.py`

核心类：

- `ReconciliationGuard`
- `ReconciliationDecision`

阻断规则：

- 对账文件缺失且 `fail_closed=True`：阻断。
- 对账文件过旧：阻断或告警。
- `success != true`：阻断。
- 任意 `qty_match == false`：阻断。
- 卖出时 `sellable_match == false`：阻断。

当前接入：

- `services/trading_pipeline/sim_task.py`
- `scripts/demo_futu_sim_submit.py`
- `scripts/demo_futu_sim_sell_submit.py`
- `services/healthcheck/checks.py`

## 9. 回测相关文件

### 单标的 CTA 回测

- Runner：`scripts/run_vnpy_backtest.py`
- 适配层：`services/backtest/vnpy_adapter.py`
- 策略桥接：`services/backtest/vnpy_strategy_bridge.py`
- 输出：`state/runs/vnpy_backtest_report.json`

当前已接入：

- `services/backtest/vnpy_strategy_bridge.py` 使用 `StrategyEngine.evaluate_bar()`。
- 离场使用 `StrategyEngine.evaluate_exit()`。

### 多标的聚合回测

- Runner：`scripts/run_vnpy_portfolio_backtest.py`
- 输出：`state/runs/vnpy_portfolio_backtest_report.json`

### 共享现金池 / 组合级约束回测

- Runner：`scripts/run_shared_cash_portfolio_backtest.py`
- 引擎：`services/backtest/portfolio_engine.py`
- 输出：`state/runs/shared_cash_portfolio_backtest_report.json`

当前已接入：

- `services/backtest/portfolio_engine.py` 使用 `StrategyEngine.evaluate_bar()`。

### 其他回测骨架

- Scaffold：`scripts/run_backtest_scaffold.py`
- 输出：`state/runs/backtest_scaffold_report.json`
- Alpha 尝试版：`scripts/run_vnpy_alpha_backtest.py`

## 10. Knot Agent 模块位置

### 评估适配层

- 主适配器：`services/evaluation_hub/adapters/knot_agent.py`
- Schema：`services/evaluation_hub/adapters/knot_agent_schema.py`
- Hub：`services/evaluation_hub/hub.py`
- 模型定义：`services/evaluation_hub/models.py`

### 运行时

- 本地运行时：`services/knot_runtime/runtime.py`
- 远程运行时：`services/knot_runtime/remote_runtime.py`

### 相关文档

- Prompt 设计：`docs/knot_agent_prompt_design.md`
- 远程批量集成：`docs/remote_knot_batch_integration.md`

## 11. 健康检查 / daily brief / 告警

### 健康检查服务

- `services/healthcheck/checks.py`
- `services/healthcheck/alerts.py`
- `services/healthcheck/__init__.py`

核心类 / 函数：

- `HealthcheckService`
- `build_alerts()`

检查项：

- Python runtime
- Futu OpenD 连通性
- Futu SDK 可用性
- 只读账户状态
- 只读持仓数量
- 只读委托数量
- reconciliation 状态
- `OrderStateStore` 统计
- paper/sim/live submit 开关
- alerts

### 脚本入口

- `scripts/run_healthcheck.py`
- `scripts/run_portfolio_brief.py`
- `scripts/run_daily_pipeline.py --mode healthcheck`
- `scripts/run_daily_pipeline.py --mode brief`

输出：

- `state/runs/healthcheck.json`
- `state/runs/portfolio_brief.json`

## 12. 当前运行产物 / 状态文件

### Candidate / Knot

- `state/runs/candidate_inputs.json`
- `state/runs/candidate_inputs.dynamic.json`
- `state/runs/remote_knot_batch_tasks.json`
- `state/runs/knot_agent_raw_output_hk.json`
- `state/runs/knot_agent_intraday_decision_hk.json`
- `state/runs/knot_agent_prompt_spec.json`
- `state/runs/knot_agent_validation_demo.json`
- `state/runs/hk_5w_candidate_refresh.json`

### 交易 / 执行

- `state/runs/hk_sim_account.json`
- `state/runs/us_sim_account.json`
- `state/runs/hk_sim_task_report.json`
- `state/runs/us_sim_task_report.json`
- `state/runs/hk_sim_close_report.json`
- `state/runs/us_sim_close_report.json`
- `state/runs/futu_sim_submit_demo.json`
- `state/runs/futu_sim_sell_submit_demo.json`
- `state/runs/futu_sim_position_reconcile.json`
- `state/runs/orders/<request_id>.json`
- `state/runs/vnpy_gateway_events_YYYYMMDD.jsonl`

### 简报 / 汇总

- `state/runs/healthcheck.json`
- `state/runs/hk_final_brief.json`
- `state/runs/portfolio_brief.json`
- `state/runs/multi_market_brief.json`

### 回测输出

- `state/runs/vnpy_backtest_report.json`
- `state/runs/vnpy_portfolio_backtest_report.json`
- `state/runs/shared_cash_portfolio_backtest_report.json`

## 13. 当前系统结构概览

### 盘前研究链

```text
CandidateProvider
-> CandidateRanker
-> EvaluationHub / Knot
-> DecisionEngine
-> ApprovalGate
-> RiskEngine
-> PaperTradeBridge
-> VnpySignalBridge / FutuPaperBridge
-> VnpyExecutor paper dry-run
-> OrderStateStore
```

### 盘中模拟交易链

```text
UnifiedCandidateProvider.load(market)
-> Futu quote snapshot
-> StrategyEngine.evaluate_candidate()
-> RiskGuard
-> ReconciliationGuard
-> SimTradingEngine local fill
-> sim task report
```

### vn.py/Futu 事件链

```text
FutuGateway
-> EventEngine
-> VnpyEventRecorder
-> VnpyGatewayEventBridge
-> OrderStateStore
```

### 回测链

```text
vn.py BacktestingEngine / PortfolioBacktestEngine
-> StrategyEngine.evaluate_bar()
-> StrategyEngine.evaluate_exit()
-> stats/report
```

### 运维链

```text
OpenDClient / FutuSdkClient / FutuAccountProvider
-> ReconciliationGuard
-> OrderStateStore.summary()
-> HealthcheckService
-> daily brief / alerts
```

## 14. 当前接入完成度

### 已完成

- candidate dynamic/static 按 market fallback。
- HK/US 盘中任务统一到 `MultiMarketSimTradingPipeline`。
- `StrategySignal` / `OrderIntent` / `OrderState` 数据协议。
- `OrderStateMachine` 和 `OrderStateStore`。
- `VnpyExecutor` paper/sim dry-run scaffold。
- vn.py 账户、持仓、订单、成交事件记录器 scaffold。
- reconciliation guard 与 Futu SIM submit 阻断。
- `StrategyRegistry` / `StrategyEngine`。
- 回测开始复用 `StrategyEngine`。
- `HealthcheckService`、`run_healthcheck.py`、daily brief、alerts。

### 仍待继续接入

- `run_hk_sim_close.py` / `run_us_sim_close.py` 抽成统一 close pipeline。
- `SimTradingEngine` 本地成交完整写入 `OrderStateStore`。
- `VnpyExecutor` 的 `sim explicit-submit` 模式，但默认仍需关闭。
- 订单持久化幂等去重。
- Futu SIM 与本地账本自动修复策略。
- 动态 candidate 历史化。
- Knot Agent 历史回放替身。
- 多市场货币/汇率处理。
- 行业/策略/市场暴露归因。
- 真正组合再平衡。

## 15. 最小接入建议

### 只接交易主流程

优先接：

- `scripts/run_hk_sim_task.py`
- `scripts/run_us_sim_task.py`
- `services/trading_pipeline/sim_task.py`
- `services/strategy/engine.py`
- `services/execution_guard/reconciliation.py`
- `services/trade_state/state_machine.py`

### 只接回测

优先接：

- `scripts/import_futu_history_to_vnpy.py`
- `scripts/run_vnpy_backtest.py`
- `services/backtest/vnpy_adapter.py`
- `services/backtest/vnpy_strategy_bridge.py`
- `services/strategy/engine.py`

### 接组合级回测

优先接：

- `services/backtest/portfolio_engine.py`
- `scripts/run_shared_cash_portfolio_backtest.py`
- `services/portfolio/risk.py`
- `services/strategy/engine.py`

### 接执行安全底座

优先接：

- `services/common/trading_models.py`
- `services/trade_state/state_machine.py`
- `services/trade_state/storage.py`
- `execution/vnpy_bridge/executor.py`
- `execution/vnpy_bridge/event_recorder.py`
- `services/execution_guard/reconciliation.py`
- `scripts/run_healthcheck.py`

## 16. 关键结论

- 当前交易入口：`scripts/run_hk_sim_task.py`、`scripts/run_us_sim_task.py`。
- 当前统一交易 pipeline：`services/trading_pipeline/sim_task.py`。
- 当前统一策略入口：`services/strategy/engine.py`。
- 当前订单状态入口：`services/trade_state/state_machine.py`。
- 当前 vn.py dry-run 执行入口：`execution/vnpy_bridge/executor.py`。
- 当前 Futu/vn.py 事件记录入口：`execution/vnpy_bridge/event_recorder.py`。
- 当前对账阻断入口：`services/execution_guard/reconciliation.py`。
- 当前健康检查入口：`scripts/run_healthcheck.py`。
- 当前 daily brief 入口：`scripts/run_portfolio_brief.py`。

系统已经从“脚本堆叠”推进到“pipeline + strategy engine + order state + reconciliation + healthcheck”的结构。下一阶段重点应是统一 close pipeline、订单幂等、SimTradingEngine 状态落盘，以及在严格开关控制下推进 `sim explicit-submit`。
