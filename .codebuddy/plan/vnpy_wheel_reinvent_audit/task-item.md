# 实施计划 — Classic Multifactor 切分支重构（vnpy 原生化）

本计划基于 `.codebuddy/plan/vnpy_wheel_reinvent_audit/requirements.md` 的需求 1-12。
执行阶段严格按 S0 → S5 偏序推进，S0 先完成清理基线（含 scripts / services / state 全面精简），
再进入功能迁移。清理顺序遵守"scripts 删除 → services 删除 → state 清理"偏序，避免孤儿 import。

- [ ] 1. 新分支初始化与清理基线（S0）
- [ ] 1.1 拉新分支 + 旧分支冻结打 tag
   - 从 `futu-dev-knot-setup` 拉出 `classic-vnpy-native-rewrite`，立即冻结旧分支
   - 在旧分支最新 commit 打 tag `classic-pre-vnpy-rewrite-v1`（最终回退凭证）
   - 新分支首个提交作为"清理基线起点"
   - _需求：3.1、6.2、12.6_

- [ ] 1.2 `scripts/classic_multifactor/` 子目录清理
   - 删除：`backtest.py` / `run_alpha_backtest.py` / `alpha_strategy.py` / `us_single_symbol_multifactor.py` / `run_vnpy_cta_sweep.py` / `run_vnpy_cta_nvda_grid.py` / `run_vnpy_cta_nvda_regime.py` / `run_vnpy_cta_nvda_tuned.py` / `run_vnpy_cta_nvda_walkforward.py` / `run.py` / `run_portfolio.py` / `run_portfolio_loop.py` / `run_loop.py` / `_loop_common.py` / `test_trade_sync.py` / `fetch_us_1m_history.py` / `nohup.out`
   - 保留白名单：`strategy.py` / `cta_backtest.py` / `model.py` / `data.py` / `fusion.py` / `external.py` / `risk.py` / `flow.py` / `account.py` / `minute_guard.py` / `config_schema.py` / `run_llm_research.py`
   - 执行 `git rm` 保留历史，完成后跑 `python -m py_compile scripts/classic_multifactor/*.py`
   - _需求：8.1、8.2、8.4_

- [ ] 1.3 `scripts/` 顶层入口批量清理
   - 删除 HK / A股主线：`run_hk_*.py`（8 个）、`reconcile_hk_live_positions.py`、`run_hk_futu_sim_session.ps1`
   - 删除 Knot 独立工具：`apply_remote_knot_candidates.py` / `prepare_remote_knot_batch_payloads.py` / `run_knot_agent_hk_refresh.py` / `run_intraday_knot_decision.py` / `write_remote_knot_result.py`
   - 删除多市场报告：`run_midday_report.py` / `run_premarket_report.py` / `run_market_recap.py` / `run_multi_market_brief.py` / `run_daily_pipeline.py` / `run_action_distribution.py` / `run_ai_usage_metrics.py` / `run_coverage_metrics.py` / `run_strategy_metrics.py` / `run_strategy_review.py`
   - 删除旧回测脚手架：`run_backtest_scaffold.py` / `run_vnpy_backtest.py` / `run_vnpy_alpha_backtest.py` / `run_vnpy_portfolio_backtest.py` / `run_shared_cash_portfolio_backtest.py`
   - 删除候选/数据工具：`generate_dynamic_candidates.py` / `check_trade_flow_status.py` / `inspect_quote_snapshot.py` / `check_futu_sdk.py` / `reconcile_futu_sim_positions.py` / `run_hk_final_brief.py` / `run_hk_sim_mark.py`
   - 保留白名单：`run_us_sim_task.py` / `run_us_sim_close.py` / `run_us_live_task.py` / `run_us_futu_sim_session.py` / `run_healthcheck.py` / `run_portfolio_brief.py` / `run_futu_sdk_probe.py` / `futu_readonly_snapshot.py` / `classic_multifactor/` / `utils/` / `README.md`
   - `import_futu_history_to_vnpy.py` 暂留，S1 回测验证后再决定是否降级到 `services/market_data/`
   - 完成后跑 `python -m py_compile scripts/**/*.py` + `scripts/run_healthcheck.py`，失败者 `git checkout <old-branch> -- <path>` 取回并登记"意外依赖"
   - _需求：10.1、10.2、10.3、10.4_

- [ ] 1.4 `services/` 子包批量清理（S0 直删部分）
   - 直接删除 10 个 services 子包及其 `__pycache__`：`sim_account/` / `candidate_engine/` / `watchlist_engine/` / `scoring_engine/` / `signals/` / `decision_engine/` / `approval_gate/` / `reporting/` / `datahub/` / `backtest/`
   - 同步删除 `tests/` 下引用以上子包的测试文件（保持编译绿灯、避免虚假通过）
   - 保留：`execution_guard/` / `risk_engine/` / `strategy/` / `evaluation_hub/` / `knot_runtime/` / `futu_account/` / `futu_opend/` / `futu_sim_trade/` / `portfolio/` / `healthcheck/` / `trading_pipeline/` / `trade_state/` / `common/`
   - `trading_pipeline/live_task.py` **暂保留**（S3a/S3b 完成后在 Task 8 删除），避免过渡期断链
   - 完成后跑 `python -m py_compile services/**/*.py scripts/**/*.py`，失败则回滚对应子包
   - _需求：11.1、11.4、11.5_

- [ ] 1.5 `state/` 历史产物清零（对账从零重建）
   - 执行前安全检查：`ps -ef | grep -E "run_(loop|us_|hk_)"` 确认无 Classic live/sim 会话在跑
   - 打印目标清单供用户审阅，获得"确认清理 state/runs/"后再执行（走 `git rm` 保留历史）
   - 删除 HK 产物：所有 `*hong_kong*` / `hk_*` / `*_hk.json`
   - 删除 A 股产物：所有 `*a_share*`（approval / drafts / paper_intents）
   - 删除 US 运行态：`us_sim_account.json` / `us_sim_task_report.json` / `us_sim_close_report.json` / `us_futu_sim_session_*.json` / `us_live_task_report.json` / `classic_multifactor_NVDA_US_live_report.json` / `loop_classic_multifactor_NVDA_US_*.jsonl` / `loop_anchor_*.json`
   - 清空 `state/runs/orders/`（对账从零）、`state/runs/strategy_selection/*.jsonl`、`state/runs/classic_multifactor/` 历史回测产物
   - 删除其他：`multi_market_brief.json` / `portfolio_us_tech_*.json` / `remote_knot_batch_tasks.json` / `shared_cash_portfolio_backtest_report.json` / `strategy_metrics.json` / `evaluation_signals.json` / `futu_drafts_*.json` / `futu_sim_position_reconcile.json` / `futu_readonly_snapshot.json` / `portfolio_brief.json` / `vnpy_*_report.json` / `vnpy_gateway_events_*.jsonl` / `hk_futu_sim_session.pid.json`
   - 删除 HK/A 股候选 & watchlist：`state/candidates/a_share.json` / `state/candidates/hong_kong.json` / `state/watchlists/a_share.json` / `state/watchlists/hong_kong.json`
   - 保留：`state/candidates/us.json` / `state/watchlists/us.json` / `state/runs/candidate_inputs/classic_multifactor_NVDA_US.json` / `state/runs/archive/`
   - 完成后跑 `scripts/run_healthcheck.py` 验证冷启动：`OrderIdempotencyGuard` 空 index 自举、`ReconciliationGuard` 冷启动哨兵写出新基线
   - _需求：12.1、12.2、12.3、12.4、12.5_

- [ ] 2. 回测路径统一到 vnpy BacktestingEngine（S1）
- [ ] 2.1 扩展 `cta_backtest.py` 支持参数扫参
   - 基于 `OptimizationSetting` 接入 `run_bf_optimization` / `run_ga_optimization`
   - 添加 `--optimize bf|ga`、`--target` 参数；复用 `ClassicMultiFactorCtaStrategy` 入参
   - _需求：1.1、2.1、4.1（S1）_

- [ ] 2.2 删除 `run_vnpy_cta_*` 系列冗余入口并校验回测基线
   - 确认 1.2 已删 5 个扫参/分段脚本；若有残留一并清理
   - 在 NVDA 近 1 年 1m / 15m 数据上对比新 `cta_backtest.py` 与旧脚本基线，差异 ≤ 2%
   - 产出回测对账报告 `state/runs/reports/cta_backtest_baseline.json`（重建后首次基线）
   - _需求：3.3、7.1、8.1_

- [ ] 3. 实盘数据面切换到 OmsEngine（S2）
- [ ] 3.1 将 `services/futu_account/FutuAccountProvider` 改为 OmsEngine 只读适配器
   - 取消对 `OpenSecTradeContext` 的直接调用，读取 `main_engine.get_all_trades/positions/account`
   - 保留原对外 API 签名，兼容 `services/trading_pipeline/live_task.py` 现有调用（过渡期）
   - _需求：1.1、2.1、5.1、11.2_

- [ ] 3.2 订阅 OmsEngine 事件替代轮询
   - 在过渡期 `live_task.py` 注册 EVENT_TRADE / EVENT_ORDER / EVENT_POSITION / EVENT_ACCOUNT 回调
   - 删除 `FutuSdkClient.accinfo_query` / `position_list_query` / `order_list_query` / `deal_list_query` 的使用点（保留 SDK 类本身作为冷启动兜底）
   - OpenD 断连兜底：push 超时 > N 秒降级为单次 poll，记录告警
   - _需求：1.1、6.1（R3）、11.2_

- [ ] 4. 分钟级常驻入口 `run_intraday_loop.py`（S3a）
- [ ] 4.1 新建 `scripts/classic_multifactor/run_intraday_loop.py` 常驻进程框架
   - 启动 `MainEngine + FutuGateway + CtaEngine`，注册 `ClassicMultiFactorCtaStrategy`（intraday mode）
   - EVENT_TIMER 驱动；支持 `--session-start` / `--session-end` / `--exit-after-session` / `--max-intraday-trades` / `--max-selected`
   - 数据粒度 1m/5m，使用 `BarGenerator` + `ArrayManager`
   - _需求：2.1、4.1（S3a）、9.1_

- [ ] 4.2 CtaEngine.send_order 前接入 execution_guard pre-hook 管道
   - 封装 `ExecutionGuardPipeline`：`OrderIdempotencyGuard` → `ReconciliationGuard` → `MinuteGuard` → `LiveRiskGate`
   - 任一 gate 拒绝时不调用 `CtaEngine.send_order`，写 `state/runs/orders/` 与 `events.jsonl`
   - `config_schema.py` 校验 `loop_mode == "intraday"` 必填字段
   - _需求：2.1、5.1、5.2、9.4_

- [ ] 5. 日线级调度入口 `run_daily_rebalance.py`（S3b）
   - 新建 `scripts/classic_multifactor/run_daily_rebalance.py`，每日调度 1-2 次
   - 单次读取 EOD 数据后一次性下单调仓，**不启** EVENT_TIMER 分钟循环
   - 风控字段以日维度为主：`daily_new_pct` / 组合敞口 / 持仓上限 / 单日最大换手
   - 复用 S3a 的 `ExecutionGuardPipeline` 与 `ClassicMultiFactorCtaStrategy`（daily mode 派生）
   - `config_schema.py` 拒绝 intraday/daily 字段混用（`loop_mode` 路由校验）
   - _需求：2.1、4.1（S3b）、9.2、9.3、9.4_

- [ ] 6. 订单状态机收敛（S4）
   - 将 `services/trade_state/state_machine.py::OrderStateStore` 改为 OmsEngine 事件只读快照记录者
   - 保留 `request_id` 幂等键与持久化落盘，状态迁移源头改为 EVENT_ORDER / EVENT_TRADE
   - 评估 `services/trade_state/strategy_state.py`：若 Classic 不再读写则删除
   - 补单测：push 乱序 / push 丢失 / 重启恢复三种场景
   - _需求：1.1、5.1、6.1（R3）、11.3_

- [ ] 7. SIM 双跑对账与上线验收（S5 前半）
   - 两套独立工作区分别跑新分支 `run_intraday_loop.py` 与旧分支 `run_loop.py`，同一份 `nvda_g09.json`
   - 连续 5 个交易日 SIM 双跑，产出日终对账脚本 `scripts/diff_dual_run.py`（成交笔数/价格/pnl/风控拦截/幂等命中）
   - 冷启动反向验证：NVDA_G09 冷启动下 `max_intraday_trades` 能正确读取 OmsEngine 今日成交
   - 分钟级 ≥ 半个交易日、日线级 ≥ 3 日各完成一次闭环
   - _需求：3.2、3.3、7.1_

- [ ] 8. 二轮清理与文档收尾（S5 后半）
   - S3a/S3b 稳定后删除 `services/trading_pipeline/live_task.py`（43KB 手写轮询路径）
   - `scripts/` 顶层第二轮扫查，确认保留集合是需求 10.2 白名单子集
   - `services/futu_account/` 不再使用的轮询入口 dead code 清理
   - 更新 `docs/system_integration_guide.md`：migration 章节、分钟/日线双入口说明、pre-hook 管道图、scripts/services 精简清单
   - README 顶部添加 migration notice 与旧分支回退指引
   - 打 tag `classic-vnpy-native-v1`，合并前人工审批 + `VNPY_LIVE_APPROVED=YES` 保留
   - _需求：4.1（S5）、7.1、7.2、10.2、10.4、11.2、11.3_
