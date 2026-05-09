# 量化交易系统接入梳理

## 1. 当前系统定位

本项目当前定位为个人港股/美股多策略选股跟踪与量化交易系统，核心边界如下：

- `scripts/`：只保留任务入口和市场配置。
- `services/`：承载候选池、策略、风控、订单状态、对账、健康检查等业务逻辑。
- `execution/`：承接 paper/vn.py/Futu 执行桥接，但默认不自动实盘。
- `state/`：保存候选、观察池、报告、订单状态、对账结果和运行快照。
- `vn.py` / `vnpy_futu`：作为行情、账户、订单、成交、回测和网关底座。

当前安全原则：

- 默认不做真实实盘提交；实盘入口默认 dry-run。
- `VnpyExecutor` 支持 `paper/sim/sim_submit/live_submit`，但 `sim_submit/live_submit` 必须显式开关。
- 真实提交需同时满足 `--live-submit`、`VNPY_LIVE_CONFIG=YES`、`VNPY_LIVE_SUBMIT=YES`，默认还要求 `VNPY_LIVE_APPROVED=YES`。
- Futu SIM 会话只操作 `TrdEnv.SIMULATE`，不得切到 REAL。
- Knot/LLM 只能产出结构化评估或策略建议，不得绕过风控、对账、审批、幂等和订单状态机。

AI / Vibe Coding 协作底座：

- 项目规则：`.codebuddy/rules/vnpy-quant-system-vibecoding-context.mdc`
- 当前主线：`scripts/` 只保留入口与参数，`services/` 放业务逻辑，`execution/` 放执行桥，`state/runs/` 放运行产物。
- 历史实验目录：`examples/` 仅作参考，不作为当前开发基线。

## 2. 交易主入口文件

### 港股交易入口

- 盘中主任务：`scripts/run_hk_sim_task.py`
  - 已改为调用 `services/trading_pipeline/sim_task.py`。
  - 已启用 `reconciliation_required=True`。
- 盘后收盘：`scripts/run_hk_sim_close.py`
- 盘中盯市/估值：`scripts/run_hk_sim_mark.py`
- 富途模拟账户会话：`scripts/run_hk_futu_sim_session.py`
  - 操作 Futu `SIMULATE` 账户，默认港股常规交易时段内轮询，支持 `--max-budget`、`--max-order-value`、`--max-loss`、`--quote-retries`。
  - 输出 `state/runs/hk_futu_sim_session_report.json` / `state/runs/hk_futu_sim_session_state.json` / `state/runs/hk_futu_sim_session.log`。
- 港股 Knot 刷新：`scripts/run_knot_agent_hk_refresh.py`
- 港股盘中 Knot 决策：`scripts/run_intraday_knot_decision.py`

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

- Futu 模拟账户会话脚本已经支持 HK/US 两地独立入口，但尚未抽成同一个参数化 session pipeline。
- Futu SIM submit 会话已写入 `OrderStateStore`，仍需继续增强成交回报轮询与撤单超时处理。

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
- 策略选择器：`services/strategy/strategy_selector.py`
- 测试：`tests/test_strategy_engine.py`

核心类：

- `StrategyDefinition`
- `StrategyRegistry`
- `StrategyEngine`
- `StrategyEvaluation`
- `StrategySelector`
- `StrategySelection`

当前默认策略：

- `raw_score_timing_v1`

当前策略选择枚举：

- `trend_following`
- `breakout_momentum`
- `pullback_buy`
- `watch_only`
- `block_trade`

当前已接入：

- `services/trading_pipeline/sim_task.py`
- `services/backtest/vnpy_strategy_bridge.py`
- `services/backtest/portfolio_engine.py`

目标：让模拟、回测、未来实盘复用同一套策略计算，避免回测和交易逻辑分叉。LLM/Knot 只负责结构化策略选择或评估，不直接决定下单数量，也不得绕过硬风控。

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

## 7. 风控与执行限制

### 7.1 max_intraday_trades限制失效问题与修复

#### 问题描述
在`run_loop.py`每5分钟启动新进程的架构下，`max_intraday_trades`限制失效，导致实际成交次数超过配置限制。

#### 根本原因
- `run_loop.py`每5分钟启动一个新的`run.py`进程
- 每个进程都从零初始化自己的`trade_times`列表
- `minute_guard`只能看到当前进程内的交易记录
- 前序进程的交易记录不会传递给后续进程

#### 解决方案：账户同步机制（2026-05-09 全面修复）
在每次`run.py`启动时从 Futu 账户拉取**真实成交流水**来驱动 minute_guard；
相比旧方案最大的差别是：**不再使用进程内持久化状态**，也不再依赖订单状态机的快照，
而是把 Futu 账户当作跨进程的唯一事实来源。

1. **FutuSdkClient 新增 `deal_list_today()`**
   - 直接调用 `deal_list_query(trd_env, acc_id)`（Futu 默认只返回当日成交）
   - 字段 `create_time` 为 Futu 官方成交时间（`'%Y-%m-%d %H:%M:%S'`）
   - 跟订单状态机解耦，不受 `submitted`→`filled` 回报延迟影响

2. **FutuAccountProvider.get_today_trades(symbol)**
   - 通过 `_normalize_symbol_key()` 统一 `NVDA.US` / `US.NVDA` / `NVDA`
   - 异常向上抛出（之前旧实现吞异常导致 minute_guard 静默失效）

3. **LiveTradingPipeline 集成**
   - 按 candidate 逐标的同步（不再只按第一个标的）
   - `last_trade_at` 自动取 `max(today_trades)`（修复了 cooldown 永远不触发的 bug）
   - 同步失败 → `fail-closed`，拒绝下单并写入 `today_trades_sync_error`
   - `no_new_entry_after` 使用交易所时区（`America/New_York` / `Asia/Hong_Kong`）而非北京时间

4. **研究参数真实落地**
   - `run.py` 会把 `nvda_g09.json` 的 `setting` 作为 `candidate["strategy_config"]`
     同时写入 `state/runs/candidate_inputs.dynamic.json`（`UnifiedCandidateProvider` 真正读取的位置）
   - `LiveTradingPipeline._candidate_guard()` 会基于 `candidate["strategy_config"]` 按
     candidate 维度覆盖默认的 `MinuteTradeGuardConfig`；策略 JSON 里的
     `max_intraday_trades/entry_cooldown_minutes/min_hold_minutes/no_new_entry_after`
     会真实生效到实盘路径（而不是被 `configs/risk/live_risk_limits.yaml` 的全局默认值淹没）
   - `--minute-profile` 改为 `setdefault` 语义：**仅在 JSON 未提供对应字段时**填入默认值，
     不再无脑覆盖 JSON 冠军参数

5. **执行流程**
   ```
   run.py
     → 读 configs/classic_multifactor/<config>.json
     → 把 setting 作为 strategy_config 写入 candidate_inputs.dynamic.json
     → LiveTradingPipeline.run()
       → UnifiedCandidateProvider.load("us")            # 读到 strategy_config
       → 逐 candidate 调用 FutuAccountProvider.get_today_trades(symbol)
       → _candidate_guard(candidate) 按 JSON 覆盖阈值
       → MinuteTradeGuard.can_enter(now_ny, trades, max(trades))
       → LiveExecutionGate（approval / risk / drawdown / exposure）
       → VnpyExecutor.execute_intent → MainEngine.send_order
       → _wait_for_order_states（轮询到 filled/rejected/cancelled 或 30s 超时）
       → MainEngine.close()
   ```

#### 配置示例
```json
// configs/classic_multifactor/nvda_g09.json
"setting": {
    "max_intraday_trades": 4,
    "entry_cooldown_minutes": 30,
    "min_hold_minutes": 20,
    "no_new_entry_after": "15:30"
}
```
```yaml
# configs/risk/live_risk_limits.yaml  —— 仅作全局默认
max_intraday_trades: 4
entry_cooldown_minutes: 30
min_hold_minutes: 20
no_new_entry_after: "15:30"
```

#### 验证方法
- 检查 `state/runs/classic_multifactor_*_live_report.json`：
  - `selected[*].today_trades_count` —— Futu 真实返回的当日成交笔数
  - `selected[*].today_trades_sync_error` —— 同步失败原因（非空则 minute_guard 会 fail-closed）
  - `selected[*].minute_guard_result` —— 反映 `{allowed, reason}`
  - `selected[*].live_gate.reasons` —— 会包含 `minute_guard:max_intraday_trades_reached` 等前缀

#### 相关改动（2026-05-09）
- `services/futu_account/sdk_client.py`: 新增 `deal_list_today()`
- `services/futu_account/provider.py`: 补 `datetime` import, 改 `get_today_trades` 用 deal_list, 新增 symbol 归一化
- `services/trading_pipeline/live_task.py`: 逐 candidate 同步, `_candidate_guard`, `_guard_now`, `_wait_for_order_states`
- `services/execution_guard/live_context.py`: 用 ctime 代替 mtime 判断今日买入通量
- `scripts/classic_multifactor/run.py`: `--minute-profile` 改 `setdefault`, 同步写 `candidate_inputs.dynamic.json`
- `scripts/classic_multifactor/run_loop.py`: 新增 `--exit-after-session` 开关

### 7.2 其他风控限制

#### 单笔订单限制
- `max_order_value`：单笔订单最大金额
- `max_single_position_pct`：单标的最大持仓比例
- `max_daily_new_position_pct`：当日新增持仓比例限制

#### 账户级限制
- `max_market_exposure_pct`：市场总暴露比例
- `max_drawdown_pct`：最大回撤限制
- `max_signal_age_seconds`：信号有效期限制

#### 执行检查
- `OrderIdempotencyGuard`：订单幂等性检查，防止重复提交
- `ReconciliationGuard`：对账检查，确保账户状态一致
- `SubmitPrecheck`：提交前检查，验证订单参数合法性

### 7.3 Classic Multifactor 主线（2026-05 审计后重构）

`scripts/classic_multifactor/` 是当前唯一被主动维护的 US 多因子主线，覆盖
回测（backtest.py / CTA sweeps）、vn.py CTA 模拟、Futu SIM 与 Futu 实盘四条路径。
审计报告（.codebuddy/plan/classic_multifactor_audit/design.md）落地后，主线内所有
"口径分叉"都被折叠到下列单一事实来源中。

#### 7.3.1 回测 → 实盘口径映射

| 维度 | 单一事实来源 | 回测 | CTA 模拟 | Futu SIM | Futu 实盘 |
| --- | --- | --- | --- | --- | --- |
| 策略参数 / 风控阈值 | `scripts/classic_multifactor/config_schema.py::ClassicMultiFactorConfig` | from_args | from_args | from_args | from_args + from_setting |
| minute guard | `MinuteTradeGuardConfig.from_setting(setting)` | ✓ | ✓ | ✓ | ✓ |
| 评分 / 入场 / 出场 | `ClassicMultiFactorModel.decide_target` | 直接调用 | 直接调用 | 直接调用 | 通过 `ClassicSignalAdapter` |
| exchange 时区 | `America/New_York`（US）/ `Asia/Hong_Kong`（HK） | ✓ | ✓ | ✓ | ✓ |
| entry_at/entry_price/highest_close | 策略内存 / `StrategyStateStore` 文件 | 内存 | 内存 | 内存 | 跨进程 JSON |
| today_trades | 回测 bar 自带 / 实盘 `get_today_trades` | N/A | N/A | Futu | Futu（TTL 缓存+3 次重试+fail-closed） |

#### 7.3.2 跨进程状态表

| 路径 | 内容 | 写入方 | 消费方 |
| --- | --- | --- | --- |
| `state/runs/strategy_state/<task>_<symbol>.json` | entry_at / entry_price / highest_close / last_trade_at | `LiveTradingPipeline._persist_strategy_state_from_states` | `_reconcile_strategy_state` 下一轮读取并与 Futu 持仓做 5% 偏差校准 |
| `state/runs/candidate_inputs.dynamic.json` | 每个 symbol 的 strategy_config（+ strategy_class/market） | `run.py live`（fcntl 独占锁 + 原子替换） | `UnifiedCandidateProvider.load(market)` |
| `state/runs/classic_multifactor_<sym>_live_report.json` | selected 行（含 today_trades_count / today_buy_notional / current_qty / entry_price / highest_close / exit_reason / classic_decision） | `LiveTradingPipeline.run_once` | `run_loop.py` breaker / `run_portfolio_loop.py` anchor |
| `state/runs/loop_anchor_<task>.json` | 当日 NAV 锚点 + env/account/market 指纹 | `_load_or_build_anchor` | `run_loop.py` / `run_portfolio_loop.py` |

#### 7.3.3 风控分层表

| 层级 | 组件 | 作用 |
| --- | --- | --- |
| 策略内出场 | `ClassicMultiFactorModel.decide_target`（ATR stop_loss / trailing / take_profit / min_hold） | 生成 SELL 信号 + `hard_exit` 标记 |
| minute guard | `MinuteTradeGuard.can_enter` / `can_exit` | max_intraday_trades / entry_cooldown / min_hold / no_new_entry_after；SELL `hard_exit=True` 绕过 min_hold |
| live_gate | `LiveExecutionGate.evaluate` | approval / budget_per_trade / market_exposure / drawdown / signal_age |
| 幂等 | `OrderIdempotencyGuard.evaluate` | `request_id = md5(task|symbol|side|exchange_date|strategy_signal_id)`；仅拦 open+filled；cancelled/rejected 可重放 |
| 对账 | `ReconciliationGuard` | 强制存在 reconciliation 文件，过期自动阻断 |
| loop anchor | `_loop_common._load_or_build_anchor` / portfolio 版本 | 当日亏损熔断、环境指纹不匹配自动重建 |

#### 7.3.4 live 评估 → 订单路径（ClassicSignalAdapter）

```
LiveTradingPipeline._build_candidate_pool(candidate)
  ├─ _position_qty_for_symbol(account, symbol)                # 从 Futu 读 current_qty
  ├─ _reconcile_strategy_state(symbol, account, trades)       # 偏差 >5% 以 Futu 为准
  ├─ _evaluate_with_classic_adapter(candidate, quote, qty, state, account)
  │     ├─ 持仓路径：decide_target(hold, current_qty>0) → SELL / HOLD
  │     └─ 空仓路径：decide_target(empty)                   → BUY / HOLD
  ├─ SELL 分支 → can_exit(hard_exit=classic_decision.hard_exit) → live_gate → idempotency
  ├─ BUY  分支 → _calc_qty → can_enter(today_trades, tz) → live_gate → idempotency
  └─ 终态写回 StrategyStateStore（BUY filled → update_on_buy；SELL 全平 → clear_on_sell）
```

#### 7.3.5 loop 公共基础

- `scripts/classic_multifactor/_loop_common.py` 提供 `parse_hhmm / in_session /
  seconds_until / read_current_nav / extract_env_fingerprint / fingerprint_mismatch /
  append_loop_log / install_signal_handlers / run_child / sleep_responsive`
  及 `LoopSignalState` 数据类。
- `run_child` 在 SIGINT/SIGTERM/超时场景都强杀残留子进程，避免 run_loop 退出后
  孤儿 `run.py` 继续下单。
- `sleep_responsive` 将长 sleep 切为 5 秒一块，保证外层 SIGTERM 在秒级生效。

#### 7.3.6 已知缺陷登记

| 需求编号 | 状态 | 说明 |
| --- | --- | --- |
| 需求 1 | ✅ 已修复 | 实盘评分链路已接入 `ClassicSignalAdapter`，不再硬编码 raw_score=0.8 |
| 需求 2 | ✅ 已修复 | LiveTradingPipeline 已补全 ATR stop_loss / trailing / take_profit 的持仓出场 |
| 需求 3 | ✅ 已修复 | `StrategyStateStore` 持久化 entry_at/entry_price/highest_close，跨进程复用 |
| 需求 4 | ✅ 已修复 | Config 统一为 `ClassicMultiFactorConfig`；所有路径使用 `MinuteTradeGuardConfig.from_setting` + exchange 时区 |
| 需求 5 | ✅ 已修复 | `_loop_common.py` 抽取共享代码；信号处理统一；子进程不孤儿化 |
| 需求 6 | ✅ 已修复 | live_task 调用 `ClassicSignalAdapter`，strategy_config 由 `run.py` 注入 |
| 需求 7 | ✅ 已修复 | `daily_new_pct` 改由 `FutuAccountProvider.get_today_trade_details` 派生，30s TTL + 3 次重试 + fail-closed |
| 需求 8 | ✅ 已修复 | `request_id` 改为 `strategy_signal_id + exchange_date`；`candidate_inputs.dynamic.json` 写入加 `fcntl.flock` |
| 需求 9 | ✅ 已修复 | 本节即为文档落地；后续缺陷应在此表追加行 |

后续如有新发现的缺陷，请在本表追加一行，并在 `design.md / tasks.md` 中同步任务编号，避免
再次分散到多个主线脚本里靠口径漂移掩盖。

## 8. vn.py / Futu 执行桥接

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

## 9. reconciliation 与执行阻断

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
- `scripts/run_hk_futu_sim_session.py`
- `scripts/run_us_futu_sim_session.py`
- `services/healthcheck/checks.py`

## 10. 回测相关文件

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

## 11. Knot Agent 模块位置

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

## 12. 健康检查 / daily brief / 告警

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

## 13. 当前运行产物 / 状态文件

### Candidate / Knot

- `state/runs/candidate_inputs.json`
- `state/runs/candidate_inputs.dynamic.json`
- `state/runs/remote_knot_batch_tasks.json`
- `state/runs/knot_agent_raw_output_hk.json`
- `state/runs/knot_agent_intraday_decision_hk.json`
- `state/runs/hk_5w_candidate_refresh.json`

### 交易 / 执行

- `state/runs/hk_sim_account.json`
- `state/runs/us_sim_account.json`
- `state/runs/hk_sim_task_report.json`
- `state/runs/us_sim_task_report.json`
- `state/runs/hk_sim_close_report.json`
- `state/runs/us_sim_close_report.json`
- `state/runs/hk_futu_sim_session_report.json`
- `state/runs/hk_futu_sim_session_state.json`
- `state/runs/us_futu_sim_session_report.json`
- `state/runs/us_futu_sim_session_state.json`
- `state/runs/futu_sim_position_reconcile.json`
- `state/runs/futu_live_position_reconcile.json`
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

## 14. 当前系统结构概览

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

Futu SIM session:
UnifiedCandidateProvider.load(market) + existing Futu SIM positions
-> quote snapshot with retry
-> StrategyEngine.evaluate_candidate()
-> max_budget / max_order_value / max_loss
-> FutuSimTradeClient.submit_limit_order(SIMULATE)
-> status check
-> OrderStateStore
-> *_futu_sim_session_report.json
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

## 15. 当前接入完成度

### 已完成

- candidate dynamic/static 按 market fallback。
- HK/US 盘中任务统一到 `MultiMarketSimTradingPipeline`。
- `StrategySignal` / `OrderIntent` / `OrderState` 数据协议。
- `OrderStateMachine` 和 `OrderStateStore`。
- `VnpyExecutor` paper/sim dry-run scaffold。
- vn.py 账户、持仓、订单、成交事件记录器 scaffold。
- reconciliation guard 与 Futu SIM submit 阻断。
- `StrategyRegistry` / `StrategyEngine`。
- `StrategySelector` 已迁入 `services/strategy/strategy_selector.py`，并接入 `StrategyEngine.evaluate_candidate()` / `evaluate_bar()`。
- `scripts/run_hk_sim_task.py` / `scripts/run_us_sim_task.py` 可直接从仓库根目录执行，并通过 `MultiMarketSimTradingPipeline` 输出 `strategy_selection`。
- 新增 `services/trading_pipeline/live_task.py`、`scripts/run_us_live_task.py`、`scripts/run_hk_live_task.py`；默认 dry-run，真实提交需同时满足 `--live-submit`、`VNPY_LIVE_CONFIG=YES`、`VNPY_LIVE_SUBMIT=YES`。
- `VnpyExecutor` 增加 `sim_submit` / `live_submit` 显式提交模式，默认仍不提交。
- `SimTradingEngine` 本地成交开始写入 `OrderStateStore`。
- 回测开始复用 `StrategyEngine`。
- `HealthcheckService`、`run_healthcheck.py`、daily brief、alerts。

### 仍待继续接入

- Futu SIM session 继续抽象成统一参数化 session pipeline。
- Futu SIM session 增强成交回报轮询与撤单超时处理。
- `VnpyExecutor` 的 `sim explicit-submit` / `live_submit` 模式默认仍需关闭，只有三重环境开关、审批、REAL 环境和 live reconciliation 同时满足才可提交。

- 订单持久化幂等去重继续强化。
- Futu SIM 与本地账本自动修复策略。
- 动态 candidate 历史化。
- `strategy_selection` 历史化与回放替身。
- Knot Agent 历史回放替身。
- 多市场货币/汇率处理。
- 行业/策略/市场暴露归因。
- 真正组合再平衡。

## 16. 最小接入建议

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
- `scripts/reconcile_hk_live_positions.py`

## 17. 关键结论


- 当前本地模拟交易入口：`scripts/run_hk_sim_task.py`、`scripts/run_us_sim_task.py`。
- 当前 Futu SIM 账户会话入口：`scripts/run_hk_futu_sim_session.py`、`scripts/run_us_futu_sim_session.py`。
- 当前实盘 dry-run/live gate 入口：`scripts/run_hk_live_task.py`、`scripts/run_us_live_task.py`。
- 当前统一交易 pipeline：`services/trading_pipeline/sim_task.py`、`services/trading_pipeline/live_task.py`、`services/trading_pipeline/close_task.py`。
- 当前统一策略入口：`services/strategy/engine.py`。
- 当前订单状态入口：`services/trade_state/state_machine.py`。
- 当前 vn.py dry-run / live-submit 执行入口：`execution/vnpy_bridge/executor.py`。
- 当前 Futu/vn.py 事件记录入口：`execution/vnpy_bridge/event_recorder.py`。
- 当前对账阻断入口：`services/execution_guard/reconciliation.py`。
- 当前健康检查入口：`scripts/run_healthcheck.py`。
- 当前 daily brief 入口：`scripts/run_portfolio_brief.py`。

系统已经从“脚本堆叠”推进到“pipeline + strategy engine + order state + reconciliation + healthcheck + Futu SIM session + live gate”的结构。下一阶段重点应是统一 HK/US session pipeline、成交回报轮询、撤单超时、订单幂等和组合级实盘风控。
