
# 实施计划

> 本清单针对 `requirements.md` 所列需求 1-9，落地为可执行的编码步骤；按"先安全、后可信、再治理"的顺序推进。

- [ ] 1. 建立 `ClassicMultiFactorConfig` 单一事实来源
   - 在 `scripts/classic_multifactor/` 下新建 `config_schema.py`，把 `ClassicMultiFactorConfig` 作为唯一 dataclass
   - 实现 `from_json / from_args / from_setting / to_setting` 以及 `add_config_args(parser)` 通用注入器
   - 为 `MinuteTradeGuardConfig` 增加 `from_setting(setting_dict)` 工厂方法，统一供四处复用
   - 为所有字段明确默认值，删除各 CLI 脚本里残留的"仅 CLI 默认"
   - _需求：4.1、6.1、6.2、6.4_

- [ ] 2. 回测 / CTA / 模拟盘 / live 四路径接入统一 Config 与 MinuteGuard 工厂
   - 重构 `us_single_symbol_multifactor.py`、`run_vnpy_cta_backtest.py`、`run_vnpy_cta_sweep.py`、`run_vnpy_cta_nvda_tuned.py`、`run_vnpy_cta_nvda_grid.py`、`run_vnpy_cta_nvda_regime.py`、`run_vnpy_cta_nvda_walkforward.py` 改为通过 `ClassicMultiFactorConfig.from_args(args)` 构造
   - 在 `backtest.py / strategy.py / flow.py / live_task.py` 中把 `MinuteTradeGuardConfig(...)` 统一替换为 `MinuteTradeGuardConfig.from_setting(setting)`
   - 让 `MinuteTradeGuard.can_enter` 接受 `exchange_tz` 参数，三条主线统一传 `America/New_York`
   - _需求：4.1、4.2、6.1、6.2、6.3、8.1_

- [ ] 3. 引入 `ClassicSignalAdapter` 把 `ClassicMultiFactorModel` 嵌入实盘评分
   - 在 `services/strategy/` 下新增 `classic_adapter.py`，包装 `ClassicMultiFactorModel.decide_target` 为 `StrategyEngine` 可插拔的评分器
   - 输出 `raw_score / allow_trade / target_qty / reason / hard_exit`，代替 live candidate 中硬编码的 `raw_score=0.8 / allow_trade=True`
   - 修改 `scripts/classic_multifactor/run.py` live 分支：candidate 只写入 `strategy_config`，不再写死 raw_score/strategy_selection
   - `LiveTradingPipeline` 在评估阶段调用 `ClassicSignalAdapter`；`warming_up / entry_not_confirmed` 明确拒单并写明原因
   - _需求：1.1、1.2、1.3、1.4、6.3_

- [ ] 4. 实盘路径补全 ATR 止损止盈与持仓出场评估
   - 在 `LiveTradingPipeline` 的评估前置步骤里增加"已持仓路径"：当 `current_qty > 0` 时用 `ClassicSignalAdapter` 跑出场分支（`atr_stop_loss / atr_trailing_stop / atr_take_profit / trailing_stop`）
   - 出场信号生成 SELL `OrderIntent`，带 `hard_exit=True` 绕过 `min_hold_minutes`，仍过 `live_gate / idempotency / today_trades` 检查
   - 在 `live_task.py` report 中新增 `exit_reason` 字段记录触发源
   - _需求：2.1、2.2_

- [ ] 5. 实现跨进程策略状态持久化 `StrategyStateStore`
   - 在 `services/trade_state/` 新建 `strategy_state.py`，以 `state/runs/classic_multifactor/<task_tag>_strategy_state.json` 为载体，字段含 `entry_at / entry_price / highest_close / last_signal / last_trade_at / confirm_bars`
   - `LiveTradingPipeline` 在 `EVENT_TRADE` 回报（BUY 成交）时调用 `update_on_buy(...)`；SELL 全平时 `clear(...)`
   - `run.py live` 启动时读取状态；如文件缺失，用 Futu 持仓成本价 + 账户成交时间重建；偏差 > 5% 以 Futu 为准并写审计日志
   - 跨日判定使用 `America/New_York` 本地日期
   - _需求：2.3、2.4、3.1、3.2、3.3、3.4_

- [ ] 6. 今日成交/买入通量统一到 Futu `deal_list_today`
   - 在 `services/futu_account/provider.py`（或等价位置）封装 `get_today_trades(symbol)`，带 30s TTL 缓存、3 次重试、失败 fail-closed
   - 重写 `LiveRiskContextBuilder._today_buy_notional` 改为从 `get_today_trades` 派生，删除对 `OrderStateStore.st_ctime` 的依赖
   - `MinuteTradeGuard` 在 live 路径的 `trade_times` 改为同一数据源派生
   - 失败时 `LiveTradingPipeline` 拒单并写 `today_trades_sync_failed:<err>`；与 `--on-error stop` 联动
   - _需求：4.3、4.4、7.1、7.2、7.4_

- [ ] 7. 改造订单幂等与 candidate 文件并发写入
   - 修改 `services/execution_guard/idempotency.py`：`request_id = hash(task_name|symbol|side|exchange_date_YYYYMMDD|strategy_signal_id)`，删除分钟精度
   - `OrderIdempotencyGuard.evaluate` 仅对 `{submitted, partial_filled, filled}` 拦截；`cancelled/rejected/failed` 允许新 `strategy_signal_id` 重放
   - 在 `scripts/classic_multifactor/run.py` 为 `candidate_inputs.dynamic.json` 读写加 `fcntl.flock` 或改为 per-symbol 分片文件，避免 portfolio 并发覆盖
   - _需求：8.1、8.2、8.3、8.4、5.3_

- [ ] 8. 在 live report 暴露关键持仓/成交/止损字段
   - `LiveTradingPipeline` 在 `selected[*]` 中补充 `today_trades_count / today_buy_notional / current_qty / entry_price / highest_close / exit_reason`
   - `run_loop.py / run_portfolio_loop.py` 读取这些字段用于 breaker / anchor 决策
   - _需求：7.3_

- [ ] 9. 抽取 `_loop_common.py` 消除 `run_loop.py` 与 `run_portfolio_loop.py` 重复
   - 新建 `scripts/classic_multifactor/_loop_common.py`，迁移 `_parse_hhmm / _in_session / _seconds_until / _handle_signal / _extract_env_fingerprint / _read_current_nav / _load_or_build_anchor / _append_loop_log / _build_child_cmd`
   - `run_loop.py / run_portfolio_loop.py` 改为注入"每轮做什么"回调 + SIGTERM/SIGINT 优雅退出 + 子进程不孤儿化
   - _需求：5.1、5.2、5.4_

- [ ] 10. 同步更新集成文档与已知缺陷登记
   - 在 `docs/system_integration_guide.md` 新增/重写 "7.x Classic Multifactor 主线" 章节：回测→实盘口径映射表、跨进程状态表、风控分层表、每条主线单一事实来源
   - 在该章节末尾加 "已知缺陷" 小节，列出未修项并对齐本文档需求编号
   - 校对与 `requirements.md` 需求编号一致
   - _需求：9.1、9.2、9.3_
