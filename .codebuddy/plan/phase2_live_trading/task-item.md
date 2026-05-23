# 实施计划 — phase2 多标美股天级别 live 交易（SIM + REAL）

> 本任务清单基于 `.codebuddy/plan/phase2_live_trading/requirements.md`。**仅本期：天级别 rebalance**；分钟级延后。优先 copy 复用 `scripts/classic_multifactor/` 中已验证的原子模块。

- [ ] 1. 搭建 `phase2/live/` 目录骨架与 OrderIntent/OrderState 数据模型
   - 创建 `phase2/live/__init__.py`、`phase2/live/tests/__init__.py`，确保 `import phase2.live` 不影响 `phase2.backtest`
   - 实现 `phase2/live/order_state.py`：`OrderIntent`、`OrderState`、`OrderStateStore`（基于 JSON 文件持久化，按 `state/runs/phase2_live/<env>/<run_id>/orders/<request_id>.json` 落盘），状态枚举 `created/validated/risk_checked/approved/rejected/submitted/accepted/filled/partially_filled/cancelled/failed`
   - 编写 `test_order_state.py`：状态机转移合法性、重启后从目录重建在途订单、相同 request_id 不重复保存
   - _需求：1.1, 1.4, 5.2, 5.5_

- [ ] 2. Copy 复用 classic_multifactor/risk.py 并改命名空间
   - 把 `scripts/classic_multifactor/risk.py` 物理拷贝到 `phase2/live/risk.py`，文件顶部加注释 `copied & adapted from scripts/classic_multifactor/risk.py`
   - 将其依赖从 `scripts.classic_multifactor.model.ClassicMultiFactorConfig/FactorSnapshot` 改为本地最小 dataclass（仅保留 `max_position_pct/max_order_value` 字段），以彻底消除对 classic_multifactor 包的 import 依赖
   - 保留 `LiveRiskGuard`（来自 `services.risk_engine`）的引用方式不变
   - 编写 `test_risk.py`：单标 BUY/SELL 的 sizing 边界、`qty_zero/invalid_price/invalid_side` 拒因、`max_order_value` 截断
   - _需求：1.5, 5.1, 5.2_

- [ ] 3. 实现 6 道安全开关验证模块
   - 实现 `phase2/live/safety.py`：`SafetySwitchResult` dataclass，`validate_switches(args, env, futu_env)` 返回缺失开关列表
   - SIM：仅校验 `--live-submit`、`FUTU_HOST`、`FUTU_PORT`、`FUTU_MARKET=US`
   - REAL：在 SIM 基础上额外强制 `VNPY_LIVE_CONFIG=YES`、`VNPY_LIVE_SUBMIT=YES`、`VNPY_LIVE_APPROVED=YES`、`FUTU_ENV=真实`、`FUTU_TRADE_PASSWORD` 非空
   - 编写 `test_safety.py`：SIM 路径、REAL 全开关通过、REAL 缺任一开关均退出非 0、敏感字段（密码）不出现在错误信息中
   - _需求：6.1, 6.2, 6.5_

- [ ] 4. 实现 LiveBroker 协议与 Futu broker 包装
   - 实现 `phase2/live/broker.py`：`LiveBroker` Protocol 定义 9 方法（connect/disconnect/unlock_trade/query_account/query_positions/place_order/cancel_order/subscribe_quote/register_order_handler）
   - 实现 `phase2/live/futu_broker.py`：基于 `OpenSecTradeContext(filter_trdmarket=TrdMarket.US, security_firm=SecurityFirm.FUTUSECURITIES)` + `OpenQuoteContext`；SIM 走 `TrdEnv.SIMULATE` 不解锁；REAL 强制先 `unlock_trade(TRADING_PWD)` 再 `TrdEnv.REAL`；连接失败指数退避（最多 5 次）；通过 `TradeOrderHandlerBase` 把 OpenD 订单回报回调出去
   - 编写 `test_broker.py`：用 mock 替代 `OpenSecTradeContext` 与 `OpenQuoteContext`，验证 SIM 路径不调 `unlock_trade`、REAL 路径必须先解锁成功才能下单、解锁失败不下单、重连阻断 place_order
   - _需求：3.1, 3.2, 3.3, 3.4, 3.5, 9.3_

- [ ] 5. 实现 LiveExecutionAdapter（与回测 futumd adapter 同接口）
   - 实现 `phase2/live/live_adapter.py`：导出与 `phase2/backtest/futumd_strategy_adapter.py` **完全同名同形参**的全部符号（`place_limit/alert/bar_close/bar_open/declare_strategy_type/declare_trig_symbol/show_variable/AlgoStrategyType/OrderSide/TimeInForce/GlobalType` 等）
   - `place_limit` 内部把入参打包为 `OrderIntent`，注入 `request_id`（由 `strategy_id+symbol+side+rebalance_date+seq` 哈希生成，保证幂等）、`execution_env`、`strategy_id`
   - 提供 `LiveAdapterBindings` 类把 adapter 绑定到指定 broker + gate pipeline + OrderStateStore；提供 `attach_to_strategy(strategy_module)` 把符号注入策略命名空间
   - 提供 `respect_live_submit` 控制是否覆盖策略源码里的 `LIVE_SUBMIT=False`
   - 编写 `test_live_adapter.py`：与 backtest adapter 的导出符号集合一致；`place_limit` 非法参数（qty<=0、price<=0、symbol 不在池）写 `events.jsonl` 且不调 broker；`respect_live_submit=False` 时翻转生效
   - _需求：2.1, 2.2, 2.3, 2.4_

- [ ] 6. 实现四级 pre-trade gate 编排
   - 实现 `phase2/live/guards.py`：`PipelineContext`、`PipelineDecision`、`ExecutionGuardPipeline`，借鉴 `scripts/classic_multifactor/execution_pipeline.py` 接线但不 import 它
   - Gate 顺序：① `IdempotencyGate`（基于 `OrderStateStore` 已有 request_id）② `ReconciliationGate`（读取本 run 目录下最近 `reconcile/*.json`）③ `SingleOrderRiskGate`（用 task 2 的 `ClassicOrderRiskManager.size_and_check`）④ `PortfolioRiskGate`（基于 `services.risk_engine.LiveRiskGuard`，组合敞口/当日新增/最大回撤）
   - 任一拒绝 → 写 `events.jsonl` 的 `order_blocked` 事件 + `OrderStateStore` 推 `rejected`；全通过 → 推 `approved`，按 `live_submit` 决定是否调 broker
   - 编写 `test_guards.py`：四种拒因路径分别落事件与状态、`live_submit=False` 永不调 broker、broker 回报推进状态机到 `accepted/filled/cancelled`
   - _需求：5.1, 5.2, 5.3, 5.4, 6.4_

- [ ] 7. 实现 DailyLiveRebalanceRunner（轻量循环，不依赖 vnpy CTA）
   - 实现 `phase2/live/runner.py`：`DailyLiveRebalanceRunner`，构造时注入 `broker / gate_pipeline / order_state_store / pool_loader / strategy_module / clock`
   - 启动流程：① 通过 `pool_loader` 加载池标的 ② 通过 broker.subscribe + `get_cur_kline` 拉取 `--bar-warmup` 根日 K（默认 60）填 adapter 历史缓存 ③ 在 `session_tz` 下等待 `--rebalance-time`，每 10 分钟心跳，SIGINT/SIGTERM 优雅退出 ④ 到时把当日 EOD 日 K 追加缓存，按池顺序调 `strategy.handle_data()` 一次 ⑤ 等 `--post-rebalance-wait-seconds`（默认 120）秒回报 ⑥ `--auto-cancel-on-eod`（REAL 强制 true）撤未成交 → `disconnect` → 写 daily_report
   - 兜底：超过 `--max-runtime-seconds`（默认 1800）强制收尾；rebalance 前后各做一次 reconcile，差异超阈值切到"只平仓不开仓"
   - 编写 `test_runner.py`：fake clock 下到达 rebalance_time 触发一次 rebalance；超时强制收尾；reconcile 差异切平仓模式；REAL 收尾必须撤所有未成交单；SIGINT 优雅退出
   - _需求：4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 7.1, 7.2, 7.3, 7.4_

- [ ] 8. 实现 CLI 入口 run_phase2_live_daily.py 与产物三态分目录
   - 实现 `phase2/runners/run_phase2_live_daily.py`：argparse 全集（参考需求 8.1 列表），按 `--futu-env` + `--live-submit` 推导 `execution_env ∈ {dry_run, futu_sim, futu_real}`
   - 在主流程开始前调 `safety.validate_switches`，REAL 缺开关直接退出非 0 + stderr 打印缺失项
   - 产物根目录：`state/runs/phase2_live/<execution_env>/<run_id>/{events.jsonl, orders/, reconcile/, daily_report.json, positions_snapshot.csv}`；run_id 缺省 `<execution_env>_<UTC时间戳>`
   - daily_report.json 字段：见需求 8.2；敏感数值按项目脱敏规则处理
   - 在 `phase2/live/tests/test_runner.py` 内补一个 CLI smoke 用例（用 mock broker、`--futu-env 模拟`、fake clock）跑通"dry_run + futu_sim"两条路径并校验产物结构
   - _需求：6.3, 6.5, 8.1, 8.2, 8.3, 8.4_

- [ ] 9. 文档与 task_list 同步
   - 在 `docs/system_integration_guide.md` 新增 phase2 阶段③（多标 SIM 天级别）与阶段④（多标 REAL 天级别）小节：CLI 示例、6 道安全开关清单、产物路径、本期不实现分钟级
   - 在 `docs/project_operation_log.md` 顶部追加 dated 记录，含变更范围、关键文件、影响摘要（脱敏）
   - 创建 `.codebuddy/task_list/phase2_live_trading.md` 作为权威进度，每条任务带勾选框、对应 plan/task 编号、对应代码文件
   - _需求：10.1, 10.2, 10.3, 10.4_

- [ ] 10. 最终回归验证（pytest + dry_run smoke）
   - 跑 `python3 -m pytest phase2/strategy/tests/ phase2/live/tests/ -q`，确保既有 75 项 + 新增 ≥ 18 项 = ≥ 93 项全绿
   - 跑 `python3 -m py_compile $(git ls-files 'phase2/live/*.py' 'phase2/runners/run_phase2_live_daily.py')` 确认无语法错误
   - 在 `--futu-env 模拟 --live-submit 假值` 的 dry_run 下做一次 smoke run（mock broker，禁止真连 OpenD），确认产物目录结构、daily_report 字段、events.jsonl 行级 JSON 合法
   - 把回归结论写入 `.codebuddy/task_list/phase2_live_trading.md`
   - _需求：1.2, 1.3, 9.1, 9.2, 9.3, 9.4_
