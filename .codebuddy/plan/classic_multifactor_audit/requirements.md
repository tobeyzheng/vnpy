
# Classic Multifactor 系统审计与优化需求文档

## 引言

本文档针对 `scripts/classic_multifactor/` 下的历史回测、模拟盘、实盘三条主线进行系统性审查，输出问题清单、交易风险清单、重复造轮子点，并把其中**必须修复/重构**的部分转化为可执行的需求。本文档**不涉及新功能开发**，仅定义对现有三条主线的纠偏、加固、整合目标。

---

### A. 当前系统结构速览

| 主线 | 入口 | 调度层 | 执行核心 |
|---|---|---|---|
| 历史回测（自研 harness） | `us_single_symbol_multifactor.py --mode backtest` | `flow.py / UsSingleSymbolClassicFlow` | `backtest.py / ClassicSingleSymbolBacktester` |
| 历史回测（官方 vnpy CTA） | `run_vnpy_cta_backtest.py` / `run_vnpy_cta_nvda_*.py` / `run_vnpy_cta_sweep.py` | 无（单脚本） | `cta_backtest.py / ClassicCtaBacktestRunner` + `strategy.py / ClassicMultiFactorCtaStrategy` |
| 历史回测（AlphaLab 多标的） | `run_alpha_backtest.py` | 无 | `alpha_strategy.py / ClassicMultiFactorAlphaStrategy` |
| 模拟盘（非 Futu SIM 会话） | `run.py sim-once --submit-sim` → `us_single_symbol_multifactor.py --mode sim-once` | `flow.py / UsSingleSymbolClassicFlow.run_sim_once` | `account.py / ClassicSimOnceFlow` + `FutuSimTradeClient` |
| 实盘 / Futu SIM 会话 | `run.py live --simulate/--live-submit` | `run_loop.py`（单标） & `run_portfolio_loop.py` → `run_portfolio.py`（多标） | `services/trading_pipeline/live_task.py / LiveTradingPipeline` |

### B. 严重问题与交易风险清单（作为下文需求的直接依据）

**B1. 回测口径与实盘口径完全不一致（头号风险）**
- 回测核心（`model.ClassicMultiFactorModel.decide_target`）走因子分 + ATR 止损 + 趋势确认的完整信号链，并直接输出 `BUY/SELL + target_qty`；而 `run.py live` 路径下 candidate 被硬编码 `"raw_score": 0.8`、`"strategy_selection": {"strategy_id": "classic_multifactor_cta", "allow_trade": True}` 后直接写入 `candidate_inputs.dynamic.json`，`LiveTradingPipeline` 消费的是 `services/strategy/engine.py` 的"通用评分/选股流程"，**跟 `ClassicMultiFactorModel` 根本不是同一个决策函数**。
- 结果：回测 G09/W1-3 赢率/回撤曲线跟实盘 NVDA 累计下 6 单、触发 market_exposure 才停的实际轨迹没有任何映射关系。
- 这是**最严重的交易风险**：任何基于回测结果调出来的冠军参数，投到实盘都是盲投。

**B2. `run_loop.py` / `run_portfolio_loop.py` 每 5 分钟重启子进程，但关键状态全在进程内**
- `signal_interval_minutes`、`entry_cooldown_minutes`、`min_hold_minutes`、`entry_at`、`trade_times`、`last_signal`、`confirm_bars` 的记忆全依赖同一个 Python 进程生命期。
- 最近那一轮把 `minute_guard` 改成查 Futu `deal_list_today` 算是补了 `trade_times` 的跨进程同步，但 `entry_at/last_signal/confirm_bars` 仍然每次重启归零，**min_hold_minutes、confirm_bars 在 live 路径仍然完全失效**。

**B3. 实盘路径没有真正的"策略止盈止损"**
- `model.decide_target()` 里的 `atr_stop_loss / trailing_stop / take_profit` 逻辑只在回测和 CtaStrategy 里运行。
- `LiveTradingPipeline` 从不把现价跟 `entry_price/highest_close` 比对，只在新一轮信号里判断"要不要再买"，**一旦开仓后账户层面没有任何软止损**，只有 `max_drawdown_pct`（NAV 层面的整体熔断）和组合层面 `--daily-loss-limit`。
- 这导致：任何一次下跌都只能靠交易所价格跌破 `LiveRiskGuard.max_drawdown_pct` 去整体保护，单标 ATR 止损完全裸奔。

**B4. 订单状态与账户状态协调不足**
- 虽然上一轮已经加了 `_wait_for_order_states`（等 filled/cancelled/rejected 或 30s 超时），但：
  - 30s 超时后仍 `MainEngine.close()`，此后补来的 `EVENT_TRADE` 全部丢失，订单会永远停在 `submitted`。
  - `_today_buy_notional` 现在用文件 `st_ctime` 判当日买入通量，但状态机的 `status` 变化会导致文件被重写，**某些文件系统上 ctime 也会被更新**（Linux 的 ctime 是 metadata-change-time，不是严格 birth time；xfs/ext4 都不保 birthtime），存在误判风险。
- 没有任何逻辑在启动时做"账户持仓 ↔ 本地 OrderStore ↔ strategy_entry_price"的三方对齐：如果 `entry_price/highest_close` 丢了，ATR 止损即便接入也算不对。

**B5. `--minute-profile` 与 `nvda_g09.json` 的 `setting` 作用域仍有裂缝**
- `run.py` 里 `--minute-profile` 已经改成 `setdefault`，但它只作用于 `setting`（也就是 `candidate["strategy_config"]`），**完全不影响** `run.py live` 里硬编码的 `register_classic_multifactor_strategy()` 的 `min_raw_score=0.55` 和 candidate 里写死的 `"raw_score": 0.8`。
- 也就是说即便你把 `entry_score` 调到 0.72，只要 candidate 还写 `raw_score=0.8`，一定能过 strategy selector 的阈值——这跟回测口径也是完全脱节的。

**B6. 风险口径割裂**
- 三个并行的风险组件，互相不知道对方存在：
  1. `risk.py / ClassicOrderRiskManager` — 回测 harness 和 `ClassicSimOnceFlow` 用
  2. `services/risk_engine/live_guard.py / LiveRiskGuard` — `LiveTradingPipeline` 用
  3. `minute_guard.py / MinuteTradeGuard` — 三处都用，但每一处的 "trade_times" 来源都不一样（回测：engine 内部生成；CTA：`on_trade` 回报；live：Futu `deal_list_today`）
- 这直接导致：修了 live 的 minute_guard，回测路径不受影响；反过来也一样。

**B7. 数据加载通道不统一**
- 自研 harness 走 `VnpyBarRepository.load_us_bars → db.load_bar_data → 可选 fetch_futu_bars`
- 官方 CTA `BacktestingEngine.load_data()` 走自己的 `database_manager.load_bar_data`
- `run_vnpy_cta_nvda_grid.py` 等脚本 `VnpyBarRepository(fetch_futu_history=False).load_us_bars(...)` 的调用事实上只是"摸一下存在性"，真正的数据加载还是 `BacktestingEngine.load_data()` 自己做——这两次加载**有可能走到不同表/不同过滤条件**。
- 分钟 K 线的交易日历、盘前盘后数据是否过滤，在自研 harness、CTA、AlphaLab 三条路径里是不同处理。

**B8. 三条主线对"盘中新开仓截止时间"的时区处理不一致**
- CTA 回测 `strategy.py`：`bar.datetime.time() >= cutoff` → 若 bar 是 UTC naive 就按原值比，若是 America/New_York 带 tz 就按美东；vnpy DB 默认存 naive UTC。
- 自研 `backtest.py`：直接用 `trade_bar.datetime`，同上。
- live `live_task.py`：已改成 `America/New_York`。
- 结果：同一条 `no_new_entry_after=15:30` 在三条路径里语义不同，导致回测的 cutoff 触发频率跟实盘不一样。

**B9. 订单幂等 + candidate 去重在 loop 架构下形同虚设**
- `request_id` 由 `md5(task_name|symbol|side|YYYYMMDDHHMM)` 生成，分钟精度；loop 5 分钟一次，同分钟内两次拉起会算同 id，**超过 1 分钟后就变成新 id**；相当于"1 分钟内幂等，之后就不幂等了"。
- `candidate_inputs.dynamic.json` 每次 `run.py` 都会把当前 symbol 条目重写，其它 symbol 保留——但"其它 symbol"是别的子进程写的，**两个进程并发写这个文件会互相覆盖**（没有文件锁）。portfolio 并发场景下直接丢候选。

**B10. 默认配置有危险的兜底**
- `risk.py / ClassicOrderRiskManager` 给 `LiveRiskGuard` 的兜底是 `max_market_exposure_pct=1.0, max_drawdown_pct=0.30`——在回测/sim-once 里 OK，但如果有人把这个类直接误用到实盘路径就相当于关了市场曝险护栏。
- `--no-approval-required` 是个纯开关，写在 `run.py` 参数里，与"是否实盘"无关联检查。任何时候加这个参数都会跳过 `VNPY_LIVE_APPROVED` 门控。

### C. 重复造轮子清单

| 重复项 | 涉及文件 | 实质功能 | 建议归一到 |
|---|---|---|---|
| MinuteTradeGuardConfig 重复构造 | `backtest.py`, `strategy.py`, `flow.py`, `live_task.py` | 同样四字段阈值，四处各自 `MinuteTradeGuard(MinuteTradeGuardConfig(...))` | 新建 `minute_guard.MinuteTradeGuardConfig.from_setting(dict)` 工厂，所有路径一处构造 |
| `ClassicMultiFactorConfig` 字段参数转发 | `us_single_symbol_multifactor.py`, `run_vnpy_cta_backtest.py`, `run_vnpy_cta_sweep.py`, `run_vnpy_cta_nvda_tuned.py`, `run_vnpy_cta_nvda_grid.py`, `run_vnpy_cta_nvda_regime.py`, `strategy.py` | 每个 CLI 重复声明同一组 20+ 个参数，手工映射到 setting dict | 定义 `configs/classic_multifactor/schema.py` 把 `ClassicMultiFactorConfig` 当作单一事实来源，所有脚本用 `from_json / from_cli` |
| NAV 锚点 + 循环调度 + 断路器 | `run_loop.py`（单标） vs `run_portfolio_loop.py`（组合） | 几乎逐行相同的 `_parse_hhmm / _in_session / _seconds_until / _handle_signal / _extract_env_fingerprint / _read_current_nav / _load_or_build_anchor / _append_loop_log / _build_child_cmd / signal 处理` | 抽一个 `scripts/classic_multifactor/_loop_common.py` 模块，组合/单标只注入"每轮做什么"的回调 |
| parse_us_symbol / futu_code 转换 | `data.py`, `live_task.py._futu_code_to_symbol`, `account.py` | 三套同语义的 symbol 归一化 | 统一到 `services/common/symbol.py`，live_task / futu_account / classic_multifactor 全部复用 |
| 今日成交 / 今日买入通量 | `live_context.py._today_buy_notional`（本地 OrderStore 文件 ctime） + `live_task._sync_today_trades`（Futu deal_list） | 两个口径可能给出不同数 | 以 Futu `deal_list_today` 为唯一事实来源，`_today_buy_notional` 也从这儿派生 |
| 回测 stat 字段白名单 | `run_vnpy_cta_nvda_tuned.py`, `run_vnpy_cta_nvda_grid.py`, `run_vnpy_cta_nvda_regime.py`, `run_vnpy_cta_sweep.py` | 四份 KEY_METRICS 各自定义、各自 `_fmt` | 归一到 `cta_backtest.py` 的一个 `summarize_stats(stats)` 函数 |
| `ClassicCtaBacktestRunner` 调用模板 | `run_vnpy_cta_backtest.py`, `run_vnpy_cta_nvda_tuned.py`, `run_vnpy_cta_nvda_grid.py`, `run_vnpy_cta_nvda_regime.py`, `run_vnpy_cta_sweep.py` | 同样的参数顺序反复拼 | `ClassicCtaBacktestRunner.run_with_setting(setting, window)` 一个高级入口 |
| external selection 占位 | `external.py / JsonlSelectionReplayProvider` vs `services/strategy/external_selection.py / ExternalStrategySelectionStore` | 两份 provider 读同一份 JSONL | 把 `external.py` 的替换成对 `services/strategy/external_selection.ExternalStrategySelectionStore` 的包装 |

### D. 本次优化的非目标（明确排除）

1. 不新增大模型/LLM 能力；
2. 不改变 Futu OpenD 对接方式；
3. 不引入新的回测框架（继续基于 vnpy CTA BacktestingEngine + 现有自研 harness）；
4. 不在本轮打通跨市场（仍仅美股 + 后续港股用同一套规范）；
5. 不新增真正的持仓管理服务（沿用 `OrderStateStore`，只是补字段）。

---

## 需求

### 需求 1

**用户故事：** 作为策略开发者，我希望"回测决策函数 = 实盘决策函数"，以便回测结论能真正迁移到实盘。

#### 验收标准
1. WHEN `LiveTradingPipeline` 处理一个 candidate THEN 它 SHALL 在评分/选股阶段直接调用 `ClassicMultiFactorModel.decide_target(...)`（或一个唯一的 wrapper），而不是依赖 `services/strategy/engine.py` 的通用评分。
2. WHEN `run.py live` 构造 candidate THEN 它 SHALL NOT 写入硬编码的 `raw_score=0.8` 和 `strategy_selection.allow_trade=True`；raw_score/allow_trade SHALL 在评估阶段由 model 输出。
3. WHEN `ClassicMultiFactorModel` 决策 "HOLD/entry_not_confirmed/warming_up" THEN live pipeline SHALL 明确拒单并在报告里写明原因，禁止走"低 raw_score 但仍过 strategy selector"的路径。
4. IF 实盘 bar 数据不足以满足 `warmup_window` THEN 系统 SHALL fail-closed 拒单，并在报告里写 `warmup_insufficient`。

### 需求 2

**用户故事：** 作为风控负责人，我希望实盘路径跟回测 CTA 一样具备"账户层面的策略止盈止损"，以便单标的不会裸奔。

#### 验收标准
1. WHEN `LiveTradingPipeline` 发现一个标的当前 `current_qty > 0` THEN 它 SHALL 基于最新行情 + 本地记录的 `entry_price / highest_close` 调用 `ClassicMultiFactorModel.decide_target(current_qty>0 分支)` 判断是否触发 `atr_stop_loss / atr_trailing_stop / atr_take_profit / stop_loss / trailing_stop / take_profit`。
2. WHEN 上述任一止损止盈触发 THEN 系统 SHALL 生成 SELL OrderIntent 并走一遍完整的 live_gate / idempotency / minute_guard（`hard_exit=True` 绕过 min_hold）。
3. WHEN 重启（每轮 `run.py`）THEN 系统 SHALL 能从本地持久化文件重建 `entry_price / highest_close / entry_at` 而不是归零。
4. IF 本地的 `entry_price` 与 Futu 账户返回的持仓成本价偏差超过 5% THEN 系统 SHALL 以 Futu 账户口径为准并写入审计日志。

### 需求 3

**用户故事：** 作为量化交易员，我希望跨 `run.py` 进程重启后，`min_hold_minutes / entry_cooldown_minutes / confirm_bars / entry_at` 依然有效，以便多次重启不会重复开仓。

#### 验收标准
1. WHEN 一次 BUY 成交回报到达 THEN 系统 SHALL 把 `entry_at / entry_price / highest_close / last_signal` 持久化到 `state/runs/classic_multifactor/<task_tag>_strategy_state.json`。
2. WHEN `run.py live` 启动 THEN 系统 SHALL 从该文件恢复状态，如文件缺失则以 Futu 账户持仓成本 + 入场时间为准重建。
3. WHEN 一次 SELL 全平完成 THEN 系统 SHALL 清零对应字段并把 `last_trade_at` 更新。
4. IF 跨日（美东日）切换 THEN `entry_cooldown_minutes / no_new_entry_after / confirm_bars` 的计时 SHALL 使用 `America/New_York` 本地时间而不是北京时间或 UTC。

### 需求 4

**用户故事：** 作为风控负责人，我希望三条主线（自研 harness / vnpy CTA / live）共用同一套 `MinuteTradeGuard` 构造与 cutoff 时区语义，以便回测 PnL 能真实代表实盘 PnL。

#### 验收标准
1. WHEN 任一路径构造 `MinuteTradeGuardConfig` THEN 它 SHALL 通过 `MinuteTradeGuardConfig.from_setting(setting_dict)` 工厂方法构造，禁止在四个位置各自 `MinuteTradeGuardConfig(max_intraday_trades=..., ...)` 复制粘贴。
2. WHEN `MinuteTradeGuard.can_enter` 比较 `now.time()` 与 `cutoff` THEN 它 SHALL 接受一个 `exchange_tz` 参数（默认 `America/New_York`），对 live/backtest/CTA 统一按"标的交易所本地时间"比较。
3. WHEN live 路径查询"今日成交" THEN 它 SHALL 仅通过 `FutuAccountProvider.get_today_trades(symbol)`（基于 `deal_list_query`）获取，**禁止**从本地 `OrderStateStore` 派生；CTA / 自研 harness 的 `trade_times` 则来自各自 engine 内部成交记录。
4. IF `get_today_trades` 抛异常 THEN live 路径 SHALL fail-closed 拒单并在 report 写 `today_trades_sync_failed:<err>`。

### 需求 5

**用户故事：** 作为系统运维，我希望所有 `run_*_loop.py` 和 portfolio 调度共用同一套循环/锚点/断路器逻辑，以便修一次 bug 两处都受益。

#### 验收标准
1. WHEN 重构完成 THEN 仓库 SHALL 只存在一个 `scripts/classic_multifactor/_loop_common.py` 模块，内含 `_parse_hhmm / _in_session / _seconds_until / _handle_signal / _extract_env_fingerprint / _read_current_nav / _load_or_build_anchor / _append_loop_log`。
2. WHEN `run_loop.py` / `run_portfolio_loop.py` 启动 THEN 它们 SHALL 仅提供"每轮怎么跑一个子任务"的回调，禁止重新实现上述公共逻辑。
3. WHEN 并发 portfolio 运行多个 symbol THEN 对 `state/runs/candidate_inputs.dynamic.json` 的写入 SHALL 使用文件锁或 per-symbol 分片文件，禁止互相覆盖。
4. IF `run_loop.py` 收到 SIGTERM/SIGINT THEN 它 SHALL 在当前子进程结束后优雅退出，不能把子进程孤儿化。

### 需求 6

**用户故事：** 作为策略开发者，我希望 CLI 参数 / JSON config / candidate.strategy_config 三处共享同一份 schema，以便改一次参数不用翻五个 run_vnpy_cta_*.py。

#### 验收标准
1. WHEN 新增或修改一个 `ClassicMultiFactorConfig` 字段 THEN 该字段 SHALL 只需要在一处定义（dataclass 本身），CLI 参数 SHALL 通过 `add_config_args(parser, ClassicMultiFactorConfig)` 自动注入。
2. WHEN `run.py sim-once / legacy-backtest / cta-backtest` 运行 THEN 它们 SHALL 通过 `ClassicMultiFactorConfig.from_args(args)` 构造 config，禁止手工逐字段转发。
3. WHEN `run.py live` 运行 THEN 它 SHALL 把 `ClassicMultiFactorConfig` 的序列化结果写入 `candidate["strategy_config"]`，而不是把 `setting` 原样透传。
4. IF config 缺字段 THEN 系统 SHALL 以 `ClassicMultiFactorConfig` 的 default 兜底；禁止出现"某字段默认值只在 CLI 里"的情形。

### 需求 7

**用户故事：** 作为交易员，我希望实盘下单前的"今日买入通量、策略止损价位、当前持仓"这些数据有**单一事实来源**，以便跨进程不会自相矛盾。

#### 验收标准
1. WHEN `LiveRiskContextBuilder._today_buy_notional` 需要计算今日买入 notional THEN 它 SHALL 从 `FutuAccountProvider.get_today_trades(symbol)` 派生，而不是从 `OrderStateStore` 的文件 mtime/ctime 派生。
2. WHEN 多个子进程同时查询今日成交 THEN 调用 SHALL 可缓存（默认 30s TTL）以避免过度调用 OpenD `deal_list_query`。
3. WHEN live 报告写入 `selected[*]` 时 THEN 每一条记录 SHALL 同时暴露 `today_trades_count / today_buy_notional / current_qty / entry_price / highest_close`，供 `run_loop.py / run_portfolio_loop.py` 做熔断判断。
4. IF Futu 查询失败超过 3 次连续重试 THEN 系统 SHALL fail-closed 拒单并触发 `--on-error stop`（如配置）。

### 需求 8

**用户故事：** 作为 vibecoding 维护者，我希望订单幂等在 5 分钟循环架构下真正有效，以便同一条"想开仓"的意图不会被连跑 6 次。

#### 验收标准
1. WHEN 一个 candidate 生成 OrderIntent THEN `request_id` SHALL 由 `(task_name, symbol, side, 交易所日期 YYYYMMDD, strategy_signal_id)` 派生，禁止使用分钟精度时间戳。
2. WHEN `OrderIdempotencyGuard.evaluate(request_id)` 发现同 id 订单在 `OrderStateStore` 中处于 `{submitted, partial_filled, filled}` 状态 THEN 它 SHALL 返回 `allowed=False, reason=idempotency_duplicate`。
3. WHEN candidate 不变但"上一条 entry 已经 SELL 出场"时 THEN 新的下一次 BUY SHALL 使用新的 `strategy_signal_id` 从而得到新的 `request_id`。
4. IF `request_id` 已存在但状态为 `cancelled / rejected / failed` THEN 系统 SHALL 允许以新的 `strategy_signal_id` 再次尝试（不能被旧失败单永久卡住）。

### 需求 9

**用户故事：** 作为项目文档维护者，我希望审计结论、三条主线口径对齐、重复造轮子治理的现状与 TODO 全部沉淀到 `docs/system_integration_guide.md`，以便后来人一份文档看清整个轮廓。

#### 验收标准
1. WHEN 上述需求 1-8 的任一项被实施 THEN `docs/system_integration_guide.md` 的"7.x Classic Multifactor 主线"章节 SHALL 同步更新。
2. WHEN 读者查阅该章节 THEN 它 SHALL 能回答：回测→实盘的口径映射表、跨进程状态表、风控分层表、每条主线的"单一事实来源"。
3. IF 仓库里还存在本次审计发现但还没修的 P0/P1 问题 THEN 文档 SHALL 以"已知缺陷"小节明确列出，附编号对齐本文档需求编号。

---

## 修复优先级建议（仅供任务规划参考，不作为验收标准）

- **P0（影响交易安全，必须先做）**：需求 1、需求 2、需求 3、需求 7
- **P1（影响回测可信度）**：需求 4、需求 6、需求 8
- **P2（治理和可维护性）**：需求 5、需求 9
