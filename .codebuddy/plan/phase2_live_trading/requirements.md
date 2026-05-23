# 需求文档 — phase2 真实连接 OpenD 的多标美股天级别量化交易

## 引言

phase2 当前已具备 **多标本地回测能力**（`phase2/backtest/portfolio_backtest_engine.py` + `phase2/runners/run_phase2_multi_backtest.py`，75 项单测全绿）。本计划的目标是在**不破坏现有回测、不修改 futumd 策略源码**、不影响 `scripts/classic_multifactor/`、`tmp/` 等其他模块的前提下，给 phase2 增加：

1. **多标美股 SIM 天级别交易**：通过 OpenD 接入 Futu 模拟账户，每个美股交易日在 `rebalance_time` 触发一次 futumd 策略，把 `place_limit` 的下单意图真实落到模拟账户。
2. **多标美股 REAL 天级别交易**：在 SIM 充分验证后，通过 6 道安全开关（CLI flag + 3 个 `VNPY_LIVE_*` 环境变量 + `FUTU_ENV=真实` + `FUTU_TRADE_PASSWORD`）启用真实账户交易，并且先解锁交易密码再下单。
3. **零策略侵入**：futumd 策略文件 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` 与既有 `phase2/backtest/futumd_strategy_adapter.py` **保持现有源码不变**；新增 `phase2/live/` 目录承载 live 适配器、broker 抽象、运行循环、CLI 入口、单测。
4. **复用 classic_multifactor 已验证模块**：把 `scripts/classic_multifactor/` 中真正与 vnpy CTA 解耦的原子工具——风控（`risk.py`）、四级 gate 编排骨架（`execution_pipeline.py`）、订单状态机字段、6 道安全开关与产物分目录范式——**物理拷贝**到 `phase2/live/` 并改命名空间，避免后续耦合 vnpy CTA 框架。
5. **完整功能闭环**：phase2 模块最终具备「多标回测 / 多标 SIM 天级别 / 多标 REAL 天级别」三条独立可运行的链路，三者共享同一份 futumd 策略源码与同一套池配置。

本文档参考依据：
- `scripts/classic_multifactor/USAGE_GUIDE.md`（6 道安全开关、execution_env 三态、产物分目录的成熟范式）
- `scripts/classic_multifactor/run_daily_rebalance.py`（天级别单次触发、`rebalance_time` 等待、`max_runtime_seconds` 兜底）
- `scripts/classic_multifactor/risk.py`（`ClassicOrderRiskManager.size_and_check`、`RiskDecision`，可整体 copy）
- `scripts/classic_multifactor/execution_pipeline.py`（四级 pre-trade gate + `events.jsonl` + 订单状态机骨架）
- `scripts/classic_multifactor/account.py`（`OpenSecTradeContext` / `OpenQuoteContext` 调用风格）
- `phase2/Futu-API-Doc-zh-Python.md`（`OpenSecTradeContext` / `TrdEnv.SIMULATE/REAL` / `unlock_trade` / `place_order` / `position_list_query` / `TradeOrderHandlerBase`）
- `phase2/backtest/futumd_strategy_adapter.py` 已确立的 `place_limit` 入参契约（symbol, price, qty, side, time_in_force）。

非目标（明确不做）：
- 不引入新策略；live 链路必须直接驱动 futumd 策略源文件。
- 不实现分钟级（intraday）持续循环；本期只做天级别 rebalance，对应 classic_multifactor 的 daily 模式。
- 不实现 HK/A 股 live；本期只支持美股。
- 不实现 LLM/KnotAgent 决策旁路；live 链路只透传策略本身的下单意图。
- 不替换 `scripts/classic_multifactor/`；该模块继续独立存在，不被 phase2 引用（通过 copy 而非 import 复用代码）。

## 需求

### 需求 1：phase2 live 目录骨架与零侵入边界

**用户故事：** 作为 phase2 维护者，我想要在 phase2 内有一个独立的 `phase2/live/` 目录承载所有 live 相关代码，以便回测代码、futumd 策略源码、`scripts/classic_multifactor/` 都不受任何破坏。

#### 验收标准

1. WHEN 安装本计划交付物 THEN 系统 SHALL 在 `phase2/live/` 下新增模块（broker、adapter、guards、risk、order_state、safety、runner、CLI、tests），不修改 `phase2/backtest/`、`phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`、`phase2/strategy/us_multi_symbol_phase2_strategy.py` 与 `scripts/classic_multifactor/` 任何文件。
2. WHEN 运行回测 CLI `python3 phase2/runners/run_phase2_multi_backtest.py --help` THEN 系统 SHALL 行为与计划落地前完全一致，且既有 75 项 phase2 单测保持全绿。
3. WHEN 运行 live CLI THEN 系统 SHALL 仅通过 `import phase2.live.*` 与 `import phase2.strategy.*`（用现有 `load_futumd_strategy` 风格的源码加载）来工作，**不得** `import scripts.classic_multifactor.*`。
4. IF live 模块在导入或运行时崩溃 THEN 系统 SHALL 不影响 `phase2.backtest` 包导入与回测运行。
5. WHEN 拷贝 classic_multifactor 模块到 phase2/live THEN 系统 SHALL 在每个被 copy 文件顶部注释中标注"copied & adapted from scripts/classic_multifactor/<name>.py"，便于后续溯源。

### 需求 2：与 futumd 策略的统一接口契约

**用户故事：** 作为策略作者，我希望回测和 live 共享同一个 `place_limit(symbol, price, qty, side, time_in_force)` 接口契约，以便 futumd 策略源码无需任何分支即可在两种链路上运行。

#### 验收标准

1. WHEN live adapter 注入策略命名空间 THEN 系统 SHALL 提供与 `phase2/backtest/futumd_strategy_adapter.py` 完全同名同形参的 `place_limit`、`alert`、`bar_close`、`bar_open`、`declare_strategy_type`、`declare_trig_symbol`、`show_variable`、`AlgoStrategyType`、`OrderSide`、`TimeInForce`、`GlobalType` 等符号。
2. WHEN 策略调用 `place_limit(symbol, price, qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)` THEN 系统 SHALL 把该意图转换为标准内部 `OrderIntent`（含 `request_id` / `symbol` / `side` / `qty` / `price` / `tif` / `strategy_id` / `execution_env`）。
3. WHEN futumd 源码出厂 `LIVE_SUBMIT=False` 上传到 Futu 平台 THEN 系统 SHALL 在 phase2 live 链路中默认翻转为 True（与 `force_live_submit` 在回测引擎里的语义一致），CLI 提供 `--respect-live-submit` 关闭翻转。
4. WHEN 注入符号被策略调用且参数不合法（qty<=0、price<=0、symbol 不在池配置中）THEN 系统 SHALL 拒绝该意图并写入 `events.jsonl`，且不向 OpenD 发任何报文。

### 需求 3：Broker 抽象与 OpenD 连接（SIM 与 REAL 双态）

**用户故事：** 作为运维者，我想要一个统一的 `LiveBroker` 抽象屏蔽 SIM/REAL 差异，以便切换账户只需改一个 CLI 参数。

#### 验收标准

1. WHEN live 模块加载 THEN 系统 SHALL 提供 `LiveBroker` 协议，定义 `connect()` / `disconnect()` / `unlock_trade()` / `query_account()` / `query_positions()` / `place_order(intent)` / `cancel_order(broker_order_id)` / `subscribe_quote(symbols)` / `register_order_handler(callback)` 9 个方法。
2. WHEN CLI `--futu-env 模拟` THEN 系统 SHALL 使用 `OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=$FUTU_HOST, port=$FUTU_PORT, security_firm=SecurityFirm.FUTUSECURITIES)` 并以 `TrdEnv.SIMULATE` 调用 `place_order` / `position_list_query`，**禁止**调用 `unlock_trade()`。
3. WHEN CLI `--futu-env 真实` THEN 系统 SHALL 在 6 道安全开关全部满足（见需求 6）后，先调用 `trade_context.unlock_trade(TRADING_PWD)` 解锁，且仅当解锁返回 `RET_OK` 时才允许后续 `place_order(trd_env=TrdEnv.REAL)`。
4. WHEN OpenD 连接失败、超时、心跳丢失 THEN 系统 SHALL 触发自动重连（指数退避，最多 5 次），并在重连成功前阻断所有 `place_order` 调用且写入 `events.jsonl` 的 `connection_lost` 事件。
5. IF 需求 3 的 broker 实现需要单测 THEN 系统 SHALL 用 mock 替代 `OpenSecTradeContext`，**禁止真单测连接 OpenD**。

### 需求 4：天级别 rebalance 单次喂数循环

**用户故事：** 作为策略运营者，我想要一个 `DailyLiveRebalanceRunner`，每个交易日在 `rebalance_time` 触发一次 futumd 策略，并把策略产生的下单意图按 pre-trade gate 后真实下到 OpenD。

#### 验收标准

1. WHEN runner 启动 THEN 系统 SHALL 通过 `OpenQuoteContext.get_market_snapshot` / `get_cur_kline` 拉取池内每个标的的最近若干根日 K 线（数量由 `--bar-warmup` 决定，默认 60），构造 adapter 的 per-symbol 历史缓存（数据契约与回测 `portfolio_backtest_engine` 一致）。
2. WHEN 当前本地时间（默认 `America/New_York`）尚未到达 `--rebalance-time` THEN 系统 SHALL 进入等待循环，每 10 分钟输出一次心跳日志，并在收到 SIGINT/SIGTERM 时优雅退出且不发起任何下单。
3. WHEN 到达 `--rebalance-time` THEN 系统 SHALL 把当日 EOD 日 K 线追加到 adapter 缓存，按池配置标的顺序依次调用 `strategy.handle_data()`（顺序与回测引擎一致），最多触发一次 rebalance。
4. WHEN 策略在 `handle_data` 中调用 `place_limit` THEN 系统 SHALL 经过 pre-trade gate（需求 5）后，调用 `broker.place_order(intent)`，并把 OpenD 返回的 `order_id` 绑定回 `request_id`。
5. WHEN rebalance 完成、且经过 `--post-rebalance-wait-seconds`（默认 120 秒）的回报等待窗口 THEN 系统 SHALL 取消所有未成交订单（默认 `--auto-cancel-on-eod=true`，REAL 模式强制为 true）、调用 `broker.disconnect()`、生成当日 daily report 并退出循环。
6. WHEN 进程整段最长运行时间超过 `--max-runtime-seconds`（默认 1800 秒）THEN 系统 SHALL 强制进入收尾流程并退出，避免僵尸进程。
7. WHEN 进程收到 SIGINT/SIGTERM THEN 系统 SHALL 在 30 秒内完成"停止订阅 → 撤所有未成交单（REAL 模式默认开启）→ 写收尾事件 → 退出"的优雅关闭。

### 需求 5：四级 pre-trade gate + 订单状态机 + 事件日志

**用户故事：** 作为风控审核者，我想要 phase2 live 与 classic_multifactor 同等强度的下单前防线，以便任何一笔单子在落到 OpenD 之前都经过完整检查。

#### 验收标准

1. WHEN 一笔 `OrderIntent` 进入 live 执行管线 THEN 系统 SHALL 依次经过 ① 幂等（同一 request_id 不重复下单）② 对账（最近一次 reconcile 报告必须存在且未过期、未发现 broker 持仓与本地持仓不一致）③ 单标风控（拷贝自 `risk.py` 的 `ClassicOrderRiskManager.size_and_check`：单标的最大仓位、最大订单金额、cash/equity 校验）④ 组合风控（单标的最大仓位比例、当日新增比例、市场总敞口、最大回撤）四道 gate。**本期不接入** minute-level 限频 gate（`MinuteTradeGuard`），因为天级别 rebalance 不需要日内冷却 / 最小持仓 / 入场截止时间。
2. WHEN 任一 gate 拒绝 THEN 系统 SHALL 不调用 `broker.place_order`、写入 `events.jsonl` 的 `order_blocked` 事件（含 `gate` / `reason` / `request_id`）、把订单状态机推进到 `rejected` 并通过 `OrderStateStore.save` 落盘。
3. WHEN 全部 gate 通过 且 `--live-submit` 真值 THEN 系统 SHALL 调用 `broker.place_order(intent)`，把状态机推进到 `submitted`，并在收到 OpenD 的 `TradeOrderHandlerBase.on_recv_rsp` 回报后推进到 `accepted` / `filled` / `partially_filled` / `cancelled` / `failed`。
4. WHEN 全部 gate 通过 但 `--live-submit` 假值 THEN 系统 SHALL 把状态机停在 `approved` 不再前进，且**绝不调用** `broker.place_order`，事件日志写 `dry_run`。
5. WHEN 进程重启 THEN 系统 SHALL 从 `OrderStateStore` 重建在途订单状态，避免相同 request_id 重复下单。

### 需求 6：6 道安全开关与产物三态分目录

**用户故事：** 作为安全审计者，我想要 REAL 模式必须同时通过 6 道独立开关、且 SIM/REAL/dry-run 产物物理隔离，以便任何意外都不会让真实账户被错误下单。

#### 验收标准

1. WHEN `--futu-env 真实` 且任一以下条件不满足 THEN 系统 SHALL 退出码非 0、明确打印缺失的开关，且**绝不**进入主循环：① CLI 显式 `--live-submit` ② 环境变量 `VNPY_LIVE_CONFIG=YES` ③ `VNPY_LIVE_SUBMIT=YES` ④ `VNPY_LIVE_APPROVED=YES` ⑤ 环境变量 `FUTU_ENV=真实` ⑥ 环境变量 `FUTU_TRADE_PASSWORD` 非空。
2. WHEN `--futu-env 模拟` THEN 系统 SHALL 仅要求 `--live-submit`、`FUTU_HOST`、`FUTU_PORT`、`FUTU_MARKET=US` 即可工作，**不要求**任何 `VNPY_LIVE_*` 与 `FUTU_TRADE_PASSWORD`。
3. WHEN 运行 live runner THEN 系统 SHALL 把产物按 `execution_env` 写入独立目录：`state/runs/phase2_live/{dry_run|futu_sim|futu_real}/<run_id>/{events.jsonl, orders/, daily_report.json, equity_curve.csv, positions_snapshot.csv}`。
4. WHEN 三态产物目录之间存在交叉引用 THEN 系统 SHALL 在文档与代码中标注禁止读写跨态产物，且 reconciliation gate 只在与当前 `execution_env` 相同的目录中查找最近报告。
5. IF 真实账户余额、订单数量、持仓金额需要写入日志或事件 THEN 系统 SHALL 按项目敏感信息脱敏规则只输出脱敏占位符或区间描述，不输出真实数值。

### 需求 7：池配置复用与对账闭环

**用户故事：** 作为策略运营者，我想要 live 链路复用回测同一份 `pool_config.yaml`，并在 rebalance 前后把 OpenD 持仓与本地状态对账，以便策略口径一致、风控 reconcile gate 有数据可用。

#### 验收标准

1. WHEN live runner 启动 THEN 系统 SHALL 通过现有 `phase2/strategy/pool_loader.py` 读取 `phase2/strategy/config/pool_config.yaml` 得到池标的列表，**不**写回该配置。
2. WHEN rebalance 前与 rebalance 完成后各执行一次对账 THEN 系统 SHALL 调用 `broker.query_positions()` 与 `query_account()`，与本地 `OrderStateStore` 推算的持仓做差异对比，把差异写入 `state/runs/phase2_live/<env>/<run_id>/reconcile/<ts>.json`，并在差异超阈值时切到"只平仓不开仓"安全模式。
3. WHEN 重启时存在历史 `reconcile/<ts>.json` THEN reconciliation gate SHALL 读取最近一份且未过期的报告作为放行依据。
4. IF 池配置中的标的在 OpenD 行情查询时返回失败 THEN 系统 SHALL 跳过该标的、写 `events.jsonl` 的 `quote_failed` 事件、不影响其它标的喂数。

### 需求 8：CLI 入口与可观测产物

**用户故事：** 作为开发者，我希望 live 链路有清晰的 CLI、与回测同风格的产物，以便运维、复盘、对账可以直接复用 phase2 既有工具链。

#### 验收标准

1. WHEN 运行 `python3 phase2/runners/run_phase2_live_daily.py --help` THEN 系统 SHALL 输出包含 `--futu-env {模拟,真实}`、`--futu-market US`、`--rebalance-time`、`--session-tz`、`--live-submit`、`--respect-live-submit`、`--pool-config`、`--strategy-path`、`--init-cash`、`--max-single-position-pct`、`--max-daily-new-position-pct`、`--max-market-exposure-pct`、`--max-drawdown-pct`、`--max-order-value`、`--auto-cancel-on-eod`、`--post-rebalance-wait-seconds`、`--max-runtime-seconds`、`--bar-warmup`、`--run-id` 在内的参数说明。
2. WHEN rebalance 完成或优雅关闭 THEN 系统 SHALL 在该 run_id 目录下生成 `daily_report.json`，含 `pool / rebalance_time / trade_count / fill_count / cancel_count / final_nav / final_cash / total_return_pct / blocked_by_gate{idempotency,reconciliation,risk,portfolio_risk} / connection_drops / reconcile_breaches`。
3. WHEN rebalance 完成 THEN 系统 SHALL 输出 `positions_snapshot.csv`（rebalance 后时刻的标的、数量、市值），列结构与回测引擎产物对齐以便复用同一个分析脚本；本期不强制输出分钟级 `equity_curve.csv`，但保留列定义占位以兼容后续分钟级扩展。
4. WHEN run_id 未指定 THEN 系统 SHALL 按 `<execution_env>_<UTC时间戳>` 自动生成。

### 需求 9：单测覆盖与回归门槛

**用户故事：** 作为质量负责人，我希望本次新增模块带有充分单测，以便第一次提交推送前能用 `pytest` 一键验证。

#### 验收标准

1. WHEN 运行 `python3 -m pytest phase2/strategy/tests/ phase2/live/tests/ -q` THEN 系统 SHALL 75（既有）+ 至少 18（新增）= ≥ 93 项全部通过。
2. WHEN 新增单测覆盖范围 THEN 系统 SHALL 至少包含：① live adapter 与 backtest adapter 接口对齐（导入兼容性）② 6 道安全开关：缺任何一道都退出非 0 ③ broker mock 下 SIM/REAL 路径分别走 `TrdEnv.SIMULATE` / 先 `unlock_trade` 再 `TrdEnv.REAL` ④ 四级 pre-trade gate 拒绝路径分别落到 `events.jsonl` 与 `OrderStateStore` ⑤ `--live-submit=False` 时 `broker.place_order` 永不被调用 ⑥ rebalance 完成后自动撤单仅在 REAL 默认开启 ⑦ reconcile 差异超阈值切到"只平仓不开仓"模式 ⑧ DailyLiveRebalanceRunner 在 fake clock 下到达 rebalance_time 触发一次 rebalance、超过 max_runtime_seconds 强制收尾。
3. WHEN 单测执行时 THEN 系统 SHALL **不允许真实连接 OpenD**，所有外部 IO 必须 mock。
4. IF 单测涉及"消耗时间"语义 THEN 系统 SHALL 用注入时钟（fake clock）而非真 sleep。

### 需求 10：文档同步与边界声明

**用户故事：** 作为新协作者，我希望 docs 与 task_list 同步反映 phase2 三链路功能，以便我能快速找到正确的 CLI 与产物路径。

#### 验收标准

1. WHEN 本计划交付 THEN 系统 SHALL 同步更新 `docs/system_integration_guide.md`，新增 phase2 阶段③（多标 SIM 天级别）与阶段④（多标 REAL 天级别）小节，列出 CLI 示例与 6 道安全开关清单，并明确"分钟级链路本期不实现"。
2. WHEN 本计划交付 THEN 系统 SHALL 在 `docs/project_operation_log.md` 顶部追加一条 dated 记录，含变更范围、关键文件、影响摘要，**不含**真实账户金额。
3. WHEN 本计划交付 THEN 系统 SHALL 在 `.codebuddy/task_list/phase2_live_trading.md` 维护权威进度，与 `phase2_multi_backtest.md` 并列。
4. WHEN 文档示例中需要给出资金或持仓数字 THEN 系统 SHALL 使用脱敏占位符（如 `{init_cash}`、`{notional_range}`），不输出真实金额。

## 边界与硬约束（与用户问题点 3「不影响其他模块」直接对应）

- **零修改清单**：`phase2/backtest/futumd_strategy_adapter.py`、`phase2/backtest/portfolio_backtest_engine.py`、`phase2/runners/run_phase2_multi_backtest.py`、`phase2/strategy/us_multi_symbol_phase2_strategy.py`、`phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`、`scripts/classic_multifactor/**`、`tmp/**` 全部不修改。
- **新增清单**（拟）：
  - `phase2/live/__init__.py`
  - `phase2/live/order_state.py`（`OrderIntent` / `OrderState` / `OrderStateStore`）
  - `phase2/live/risk.py`（**copy 自** `scripts/classic_multifactor/risk.py`，命名空间改为 `phase2.live.risk`）
  - `phase2/live/safety.py`（6 道安全开关验证）
  - `phase2/live/broker.py`（`LiveBroker` 协议）
  - `phase2/live/futu_broker.py`（基于 `OpenSecTradeContext` + `OpenQuoteContext` 的实现）
  - `phase2/live/live_adapter.py`（与 `phase2/backtest/futumd_strategy_adapter.py` 同接口的 live 注入器）
  - `phase2/live/guards.py`（4 级 gate 编排，**借鉴** `scripts/classic_multifactor/execution_pipeline.py` 的接线方式）
  - `phase2/live/runner.py`（`DailyLiveRebalanceRunner`，轻量循环，不依赖 vnpy CTA）
  - `phase2/runners/run_phase2_live_daily.py`（CLI 入口）
  - `phase2/live/tests/test_safety.py`、`test_live_adapter.py`、`test_risk.py`、`test_guards.py`、`test_broker.py`、`test_runner.py`、`test_order_state.py`。
- **配置复用**：`phase2/strategy/config/pool_config.yaml` 只读复用，不引入新格式。
- **三链路对照**：

  | 链路 | 入口脚本 | execution_env | 频率 | 是否连 OpenD | 是否真下单 |
  | --- | --- | --- | --- | --- | --- |
  | 多标回测 | `runners/run_phase2_multi_backtest.py` | dry_run | 历史日级 | 否 | 否 |
  | 多标 SIM 天级 | `runners/run_phase2_live_daily.py --futu-env 模拟` | futu_sim | 每交易日 1 次 | 是 | 模拟账户 |
  | 多标 REAL 天级 | `runners/run_phase2_live_daily.py --futu-env 真实` | futu_real | 每交易日 1 次 | 是 | **真实账户**（6 道开关全开） |
  | 分钟级 | （后续扩展点，本期不实现） | — | — | — | — |

- **与项目规则衔接**：本计划严格遵守 `.codebuddy/` 三条项目规则——执行前确认、自动提交推送（仅在用户明确说"确认提交推送"后执行）、状态/历史/敏感信息/文档同步规则。
