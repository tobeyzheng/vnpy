# 需求文档 — Classic Multifactor 切分支重构可行性评估（基于 vnpy 生态）

## 引言

用户计划切一个新分支，把 `scripts/classic_multifactor/` 下的功能用 vn.py 生态（已在仓库
与 Python 环境内可用的 `vnpy` 核心 + `vnpy_ctastrategy` + `vnpy_futu`）重写一遍，
以消除当前自研路径带来的一系列问题（账户不同步、max_intraday_trades 失效、订单状态机
与 OmsEngine 割裂、自写单合约回测与撮合等）。本次规划的目标是：

1. **先做可靠性与完整性对标**：对每一块当前自研的能力，判断 vnpy 对应模块是否比自研的
   更可靠、更完整、生态更稳定。
2. **再给出可验证的重构路线**：明确哪些模块必须迁移、哪些暂缓、哪些可并存，以及每一步
   的前置开关、回归基线、风险点。
3. **守住生产底线**：Classic Multifactor NVDA_G09 真实实盘是当前用户线上核心任务之一
   （项目规则 2、3），任何重构都不得降低实盘可用性，迁移期必须支持"新旧双跑 + 报告
   对账"。

### 本轮关键决策（由用户最新反馈固化）

- **D1 旧分支冻结**：旧分支 `futu-dev-knot-setup` 作为稳定基线保留，不再在其上开发新功能，
  仅接受紧急线上 hotfix。新分支重构过程中如需要历史文件，用 `git checkout <old-branch> -- <path>`
  逐文件取回，不做全量合并。
- **D2 新分支大胆精简**：新分支默认**删除**当前 `scripts/classic_multifactor/` 下与 vnpy 生态
  功能重叠、已知不再使用、或已被 vnpy 原生路径取代的文件；**仅保留 KnotAgent / LLM 相关逻辑
  以及下游业务所依赖的 raw_score / candidate / external / fusion 链路**。被删除的历史文件不
  做任何备份，需要时从旧分支逐文件取回。
- **D3 分钟级与日线级拆分**：实盘循环拆成两条独立入口，分钟级（日内多次、带 `--session-end` /
  `--max-intraday-trades`）和日线级（EOD 调仓或每日 1-2 次），两者在**调度频率、数据粒度、
  风控窗口、订单节奏、配置 schema 字段子集**上有明确差异；不再以同一个 `run_loop.py` 兼容
  两种形态。
- **D4 Knot 路径仍是"旁路建议"**：KnotAgent / LLM 只提供结构化候选或过滤建议（`external.py` /
  `fusion.py` / `run_llm_research.py`），不得直接下单、不得绕过风控（与项目规则 3 一致）。

### 调研基线（本节作为结论依据，不可改动）

| 检查点 | 现状证据 |
| --- | --- |
| `vnpy_ctastrategy` 是否安装 | ✅ `/usr/local/lib/python3.11/site-packages/vnpy_ctastrategy/`，`engine.py` 970 行、`backtesting.py` 1267 行、`template.py` 496 行 |
| `vnpy_futu` 是否可用 | ✅ 仓库内嵌 `/projects/vnpy/vnpy_futu/futu_gateway.py` 748 行，覆盖 subscribe / send_order / cancel_order / query_account / query_position / query_order / query_trade / query_history |
| `BacktestingEngine` 能力范围 | ✅ 内置 limit / stop 撮合、`calculate_statistics`、`run_bf_optimization` / `run_ga_optimization`、`show_chart`，与 CtaTemplate 对接 |
| 项目已有 CtaTemplate 适配 | ✅ `scripts/classic_multifactor/strategy.py::ClassicMultiFactorCtaStrategy(CtaTemplate)` 已存在 |
| 项目已有 vnpy 集成脚本 | ✅ `run_vnpy_cta_backtest.py` / `run_vnpy_cta_sweep.py` / `run_vnpy_cta_nvda_grid.py` / `run_vnpy_cta_nvda_regime.py` / `run_vnpy_cta_nvda_walkforward.py` / `run_vnpy_cta_nvda_tuned.py` 共 6 个脚本，总行数 ≈ 1900 |
| 当前自研路径总规模 | `scripts/classic_multifactor/` 共 27 个 py 文件 / 5793 行；核心依赖 `services/` 下 `trading_pipeline` / `trade_state` / `execution_guard` / `futu_account` / `futu_sim_trade` / `strategy` 等 |
| 双通道账户同步事实 | `live_task.py` 第 275-280 行启动 `MainEngine + FutuGateway`，但从不调用 `main_engine.get_all_trades/positions/account`；另起 `FutuSdkClient` 轮询 OpenD |
| `vnpy_paperaccount` / `vnpy_riskmanager` / `vnpy_portfoliomanager` | ❌ 未安装，本次重构不依赖 |
| Knot / LLM 在 classic_multifactor 下的依赖点 | `external.py`（KnotAgent selection replay）、`fusion.py`（deterministic + knot 融合）、`run_llm_research.py`（独立研究入口）；其余文件无 Knot 直接依赖 |

## 需求

### 需求 1：明确回答"vnpy 可靠性是否优于当前自研"

**用户故事：** 作为项目负责人，我希望得到一份逐模块的对标结论，明确每个自研模块与
vnpy 对应模块的成熟度、功能覆盖、测试覆盖、社区活跃度对比，以便判断切分支是否值得。

#### 验收标准

1. WHEN 本文档输出结论时 THEN 系统 SHALL 对下表每一行给出 `[vnpy 更优]`、`[自研更优]`、
   `[平手需适配]` 三选一的明确判定，并附 1-2 行证据：

   | 能力域 | 当前自研 | vnpy 对应 |
   | --- | --- | --- |
   | Futu gateway | `services/futu_account/FutuSdkClient` + 直接 `OpenSecTradeContext` 轮询 | `vnpy_futu.FutuGateway`（748 行，push + poll）|
   | 账户 / 持仓 / 订单 / 成交同步 | `FutuAccountProvider` + 30s TTL 缓存 | `OmsEngine` 事件驱动缓存 |
   | 订单生命周期状态机 | `services/trade_state/state_machine.py` | `Status.SUBMITTING/NOTTRADED/...` + `active_orders` + `OffsetConverter` |
   | 回测撮合 | `scripts/classic_multifactor/backtest.py`（191 行单合约撮合）| `BacktestingEngine`（limit/stop + 统计 + 扫参）|
   | 策略模板 | 自由函数式 | 已有 `ClassicMultiFactorCtaStrategy(CtaTemplate)` |
   | K 线 / 指标滑窗 | `model.py` 手写 pandas 滑窗 | `BarGenerator` + `ArrayManager` |
   | 参数扫参 | 自写 `run_vnpy_cta_sweep.py`（441 行）| `OptimizationSetting` + `run_bf_optimization` / `run_ga_optimization` |
   | 事件总线 | 手写 `_wait_for_order_states` 轮询 | `EventEngine.register(EVENT_*)` |
   | 实盘调度循环 | `run_loop.py` 每 5 分钟起新进程 | 常驻 MainEngine + EVENT_TIMER |
   | 持久化 / 状态快照 | `OrderStateStore` 文件 + flock | `CtaEngine.sync_strategy_data` + json |

2. WHEN 判定完成时 THEN 系统 SHALL 识别出至少 2 项"自研更优必须保留"的能力，作为
   重构硬约束（例如 `OrderIdempotencyGuard` 的 `request_id` 幂等、`ReconciliationGuard`
   的独立对账文件、Classic 信号/选股/raw_score 业务逻辑、KnotAgent external/fusion 旁路）。

3. IF 某能力 vnpy 判定更优 THEN 系统 SHALL 追加说明"生产替换的阻塞项"（例如时区、
   合约代码映射 `NVDA.US` ↔ `vt_symbol`、CNY/USD 账户币种处理）。

### 需求 2：重构目标与范围边界

**用户故事：** 作为架构师，我希望明确新分支的"目标形态"，避免重构中途偏航。

#### 验收标准

1. WHEN 定义目标形态时 THEN 系统 SHALL 采用如下架构：
   - **回测**：唯一入口 `cta_backtest.py`（基于 `BacktestingEngine` + `ClassicMultiFactorCtaStrategy`）；
     删除 `scripts/classic_multifactor/backtest.py` 与 `run_alpha_backtest.py`；参数扫参统一到
     `cta_backtest.py --optimize`，不再维护 `run_vnpy_cta_sweep.py` / `run_vnpy_cta_nvda_grid.py` /
     `run_vnpy_cta_nvda_regime.py` / `run_vnpy_cta_nvda_tuned.py` / `run_vnpy_cta_nvda_walkforward.py`
     这 5 个重复入口（保留一个作为示例或全部收敛，视代码重用情况定）。
   - **实盘 / SIM（分钟级）**：入口 `run_intraday_loop.py`（暂定名），常驻进程，
     启动 `MainEngine + FutuGateway + CtaEngine`，Classic 分钟级策略以 CtaTemplate 注册；
     EVENT_TIMER 驱动 + session-end / exit-after-session；带 `--max-intraday-trades` /
     `--max-selected` / 分钟级风控窗口。
   - **实盘 / SIM（日线级）**：入口 `run_daily_rebalance.py`（暂定名），每日调度 1-2 次，
     读取最新 EOD 数据一次性下单调仓，无分钟级循环；风控以日维度为主（daily_new_pct、
     组合敞口、持仓超限）。
   - **账户/持仓/成交**：一切读取走 `main_engine.get_*`；`FutuAccountProvider` 改为
     `OmsEngine` 的只读适配器，不再独起 `OpenSecTradeContext`。
   - **分钟守卫 / 风控 / 对账 / 幂等**：保留在 `services/execution_guard/` 与
     `services/risk_engine/`；`CtaEngine.send_order` 前增加 pre-hook 调用这些 gate。
   - **Knot / LLM 旁路**：保留 `external.py` / `fusion.py` / `run_llm_research.py`；
     接入方式改为从 `CtaEngine` 信号生成前读取 `external_selection` 或 `knot_agent_raw_output_*.json`，
     不直接下单。

2. WHEN 定义不变项时 THEN 系统 SHALL 列出"本次重构不动"的清单：
   - KnotAgent / LLM 评估链路（`services/knot_runtime/`、`services/evaluation_hub/`、
     `scripts/classic_multifactor/run_llm_research.py`）。
   - 候选池生成 `services/strategy/candidate_provider.py` 及 `candidate_inputs*.json`。
   - `raw_score` / `strategy_selector` / `external_selection` 业务逻辑。
   - Futu SIM 会话入口（`run_hk_futu_sim_session.py` / `run_us_futu_sim_session.py`）。
   - `configs/classic_multifactor/nvda_g09.json` 等配置键名与语义（但允许**新增**
     `loop_mode: intraday|daily` 字段用于路由到分钟/日线入口）。

3. IF 任一项目主线规则被触碰 THEN 系统 SHALL 在需求中显式标注并要求审批：
   - 硬开关（`VNPY_LIVE_CONFIG/SUBMIT/APPROVED`）不得削弱。
   - `examples/` 历史示例不得成为新基线。
   - Knot/LLM 仍只提供结构化建议，不得直接下单。

### 需求 3：分支策略与新旧双跑

**用户故事：** 作为运维，我希望重构期间主分支的 NVDA_G09 实盘不中断，新分支迭代到
稳定后才切换。

#### 验收标准

1. WHEN 设计分支策略时 THEN 系统 SHALL 采用：
   - 新分支名建议 `classic-vnpy-native-rewrite`，从当前 `futu-dev-knot-setup` 拉出后
     **立即冻结旧分支**（旧分支不再合并新功能，仅接受实盘 hotfix）。
   - 新分支开发期间**不做**每日 rebase 旧分支；旧分支如有 hotfix，用
     `git cherry-pick` 按需挑回新分支。
   - 需要取回旧文件时，使用 `git checkout <old-branch> -- <path>` 单文件取回，不做全量合并。

2. WHEN 定义双跑验证时 THEN 系统 SHALL 要求至少 5 个交易日的 SIM 双跑对比：
   - 两条分支**分别在两套独立工作区/容器**内运行（避免 `state/runs/` 串扰）。
   - 同一组 nvda_g09 配置，比较：成交笔数、成交价格、持仓曲线、pnl、风控拦截次数、
     幂等命中次数；日终产出对账报告。

3. WHEN 定义切换门槛时 THEN 系统 SHALL 要求：
   - 回测基线对齐：同一段历史数据，新旧回测结果 sharpe、max_drawdown、win_rate
     差异 ≤ 2%（浮点容忍）。
   - SIM 双跑对账 0 差异或仅有可解释差异（例如时间戳颗粒度不同）。
   - NVDA_G09 实盘切换必须人工审批 + 额外 hardcode 保留回退脚本。

### 需求 4：分阶段任务切片

**用户故事：** 作为开发，我希望重构被切分为可独立提交、可独立回滚的 4-6 个阶段，
每个阶段都有独立的验收脚本。

#### 验收标准

1. WHEN 划分阶段时 THEN 系统 SHALL 采用如下阶段（每阶段单独 PR）：
   - **S0 新分支初始化与清理**：拉出新分支 → 按需求 8 清单删除旧文件 → 保留 Knot 链路与
     必要的 `services/` 红线模块 → 提交"清理基线"tag。
   - **S1 回测路径统一到 vnpy**：删 `backtest.py` / `run_alpha_backtest.py`、把 `run_vnpy_cta_*`
     系列入口收敛到 `cta_backtest.py`（支持 `--optimize` 扫参）。
   - **S2 实盘数据面切换到 OmsEngine**：`FutuAccountProvider` 改只读适配器、订阅
     EVENT_TRADE/ORDER/POSITION/ACCOUNT、删 `FutuSdkClient` 的 `accinfo_query` /
     `position_list_query` / `order_list_query` / `deal_list_query` 使用点。
   - **S3a 分钟级入口 `run_intraday_loop.py`**：ClassicMultiFactorCtaStrategy（分钟级）
     注册进 MainEngine，send_order 前 pre-hook 接入 idempotency / reconciliation /
     minute_guard / live_gate；session-end / exit-after-session / max-intraday-trades / max-selected 保留。
   - **S3b 日线级入口 `run_daily_rebalance.py`**：每日调度 1-2 次，EOD 数据一次性下单调仓；
     不启分钟级循环；风控以日维度为主。
   - **S4 订单状态机收敛**：`OrderStateStore` 降级为 OmsEngine 事件的只读快照记录者。
   - **S5 清理与文档**：删旧 `run_loop.py` / `run_portfolio_loop.py` / `run_portfolio.py` /
     `run.py`（除非仍被 Knot 链路依赖）、`events.jsonl` 多余分支；更新
     `docs/system_integration_guide.md` 与 README，顶部加 migration notice。

2. WHEN 每个阶段完成时 THEN 系统 SHALL 要求：
   - 通过 `python -m py_compile` 与现有 pytest（如果该阶段涉及 services）。
   - 在 SIM 环境上完成一次完整 `run_us_sim_task` 或 `run_hk_sim_task` 对账。
   - 提交信息包含阶段编号与回退指引。

3. IF 某阶段引入的回归无法 24 小时内修复 THEN 系统 SHALL 允许该阶段单独 revert，
   不影响下游阶段（阶段间保持 S0 → S5 的偏序依赖，S2/S3 不可前置到 S1 之前）。

### 需求 5：必须保留的自研能力（重构红线）

**用户故事：** 作为风控，我希望明确哪些自研模块在任何情况下都不能被 vnpy 替代删除。

#### 验收标准

1. WHEN 列出红线模块时 THEN 系统 SHALL 包含：
   - `services/execution_guard/idempotency.py::OrderIdempotencyGuard`（`request_id` 幂等，
     包含 `md5(task|symbol|side|exchange_date|strategy_signal_id)`，vnpy 未提供）。
   - `services/execution_guard/reconciliation.py::ReconciliationGuard`（独立对账文件 +
     冷启动哨兵，vnpy 未提供）。
   - `services/risk_engine/live_guard.py::LiveRiskGate`（market_exposure、symbol_drawdown、
     daily_new_pct、signal_age 等，vnpy_riskmanager 未安装，功能也不完全对齐）。
   - `services/strategy/` 下候选池、raw_score、strategy_selector、external_selection。
   - `services/evaluation_hub/` 与 `services/knot_runtime/`。
   - `scripts/classic_multifactor/external.py` / `fusion.py` / `run_llm_research.py`（Knot 旁路）。
   - `configs/classic_multifactor/*.json` schema（`config_schema.py`）。

2. WHEN 红线模块与 vnpy 对接时 THEN 系统 SHALL 采用"适配器 / pre-hook"模式：
   例如在 `CtaEngine.send_order` 封装上套一层 `ExecutionGuardPipeline`，拒绝时
   直接不调用 `CtaEngine.send_order`。

3. IF 红线模块需要调整 THEN 系统 SHALL 要求单独需求文档，不在本次重构内执行。

### 需求 6：风险清单与回退预案

**用户故事：** 作为 SRE，我希望了解本次重构的主要风险点和对应的回退办法。

#### 验收标准

1. WHEN 列出风险时 THEN 系统 SHALL 至少覆盖：
   - **R1 时区 / 交易日对齐**：vnpy 默认本地时区，Futu US 使用 America/New_York；
     回测 / 实盘双路径必须对齐，否则 minute_guard / daily_new_pct 会错乱。
   - **R2 合约代码映射**：项目用 `NVDA.US`、vnpy 用 `NVDA.SMART` 或 `vt_symbol` 形式；
     需要集中映射层。
   - **R3 OpenD 推送可靠性**：`FutuGateway` 依赖 push；若断连，OmsEngine 缓存可能陈旧；
     需要保留 poll 兜底（但不再每 5 分钟全量）。
   - **R4 CtaEngine 串行锁**：一个 MainEngine 进程内多策略共享一个事件循环；若某
     on_bar 阻塞，会拖慢所有策略；需要评估 NVDA_G09 + 未来其他标的组合。
   - **R5 Futu 融资账户币种**：NVDA_G09 实盘是融资 USD 账户，`FutuGateway.query_account`
     字段与 `AccountData` 映射需要在新分支逐字核对。
   - **R6 回测数据源**：`VnpyBarRepository` 目前是 CSV，需要确认与 `BacktestingEngine.load_data`
     对接方式。
   - **R7 清理误删**：新分支大胆删文件时，可能误删仍被 Knot 旁路或 `services/` 间接引用的
     文件；需要在 S0 合入前跑 `python -m py_compile` + 关键入口冒烟。

2. WHEN 定义回退预案时 THEN 系统 SHALL 要求：
   - 旧分支保留 tag（例如 `classic-pre-vnpy-rewrite-v1`）并**冻结**，作为最终回退基线。
   - 新分支切换实盘后 14 个自然日内，旧分支脚本可立即回切（保留 `configs/`、保留
     `run_us_live_task.py` 调用链）。
   - 严重事故（账户异常 / 订单错位 / 对账差异 > 1%）时立即停机 + 切回旧分支。

3. IF 切换后出现 R1-R7 任一风险显化 THEN 系统 SHALL 要求先停机、不得尝试在 live
   路径热修复。

### 需求 7：验收与上线门槛

**用户故事：** 作为项目负责人，我希望有客观标准判断重构是否完成。

#### 验收标准

1. WHEN 定义完成标准时 THEN 系统 SHALL 要求：
   - 所有 S0-S5 阶段合入新分支。
   - 回测基线差异 ≤ 2%（在 NVDA 近 1 年 1m / 15m 数据上）。
   - SIM 双跑 5 个交易日 0 显著差异。
   - 新 live 路径在 NVDA_G09 冷启动下，能正确读取 OmsEngine 今日成交并让
     max_intraday_trades 生效（旧路径的关键 bug 场景反向验证通过）。
   - 分钟级入口与日线级入口各自通过一次完整 SIM 闭环（分钟级 ≥ 半个交易日，日线级 ≥ 3 日）。
   - `docs/system_integration_guide.md` 更新完毕，README 顶部有 migration notice。

2. WHEN 发布到主分支时 THEN 系统 SHALL 要求：
   - 合并前再做一次 `scripts/run_healthcheck.py` + `run_portfolio_brief.py` 检查。
   - 合并后立即打 tag `classic-vnpy-native-v1`。
   - 人工审批 + `VNPY_LIVE_APPROVED=YES` 保留。

3. IF 上述任一条件不满足 THEN 系统 SHALL 阻止合并到主分支，分支可长期停留待修。

### 需求 8：新分支清理清单（旧文件删除/保留白名单）

**用户故事：** 作为开发，我希望在 S0 阶段有一份明确的"删 / 留 / 改"清单，避免靠临场判断漏删或误删。

#### 验收标准

1. WHEN 定义**删除**清单时 THEN 系统 SHALL 在 S0 阶段直接删除下列文件（需要时从旧分支取回）：
   - `scripts/classic_multifactor/backtest.py`（自研单合约撮合，被 `BacktestingEngine` 取代）。
   - `scripts/classic_multifactor/run_alpha_backtest.py`、`alpha_strategy.py`（旧 alpha 单标的路径）。
   - `scripts/classic_multifactor/us_single_symbol_multifactor.py`（旧单标的演示入口）。
   - `scripts/classic_multifactor/run_vnpy_cta_sweep.py` / `run_vnpy_cta_nvda_grid.py` /
     `run_vnpy_cta_nvda_regime.py` / `run_vnpy_cta_nvda_tuned.py` / `run_vnpy_cta_nvda_walkforward.py`
     （5 个扫参/分段入口，收敛到 `cta_backtest.py --optimize`）。
   - `scripts/classic_multifactor/run.py`、`run_portfolio.py`、`run_portfolio_loop.py`、
     `run_loop.py`（旧调度/每 5 分钟起子进程路径；由 S3a/S3b 的新入口替代）。
   - `scripts/classic_multifactor/_loop_common.py`（仅被旧 run_loop 使用，随其一起删）。
   - `scripts/classic_multifactor/test_trade_sync.py`（临时调试脚本）。
   - `scripts/classic_multifactor/fetch_us_1m_history.py`（历史抓取脚本，已有 `services/market_data` 替代；
     如仍在用则降级为 `services/market_data/` 下的内部工具）。
   - `scripts/classic_multifactor/nohup.out`（运行时日志残留）。

2. WHEN 定义**保留**白名单时 THEN 系统 SHALL 保留下列文件：
   - `strategy.py`（`ClassicMultiFactorCtaStrategy`，vnpy CtaTemplate 适配，本次重构核心）。
   - `cta_backtest.py`（vnpy 回测薄入口，S1 收敛目标）。
   - `run_vnpy_cta_backtest.py`（可选保留，作为 `cta_backtest.py` 的旧别名；S1 合并后删除）。
   - `model.py`、`data.py`、`fusion.py`、`external.py`、`risk.py`、`flow.py`、`account.py`、
     `minute_guard.py`、`config_schema.py`（业务逻辑，暂不重写）。
   - `run_llm_research.py`（Knot/LLM 独立研究入口，按项目规则保留）。

3. WHEN 定义**新增**清单时 THEN 系统 SHALL 在新分支内新增：
   - `run_intraday_loop.py`（S3a，分钟级常驻入口）。
   - `run_daily_rebalance.py`（S3b，日线级调度入口）。
   - `docs/system_integration_guide.md` 的 migration 章节（S5）。

4. IF 删除某文件后 `python -m py_compile scripts/classic_multifactor/*.py` 或
   `scripts/run_healthcheck.py` 冒烟失败 THEN 系统 SHALL 要求立刻补回该文件并在需求文档中
   标注"意外依赖"，作为后续清理候选。

### 需求 9：分钟级与日线级双运行形态

**用户故事：** 作为开发，我希望分钟级和日线级策略各有独立入口和独立配置子集，
避免一个 `run_loop.py` 既跑分钟又跑日线导致语义混乱。

#### 验收标准

1. WHEN 定义**分钟级入口**时 THEN 系统 SHALL 满足：
   - 入口：`scripts/classic_multifactor/run_intraday_loop.py`。
   - 调度：常驻进程；`MainEngine + FutuGateway + CtaEngine`；EVENT_TIMER 驱动；
     `--session-start` / `--session-end` / `--exit-after-session` 保留。
   - 数据粒度：1m 或 5m；`BarGenerator` + `ArrayManager`。
   - 风控字段：`max_intraday_trades` / `max_selected` / 分钟级 drawdown / 单次下单笔数上限。
   - 配置路由：`configs/classic_multifactor/*.json` 中 `loop_mode == "intraday"` 时由此入口消费。
   - 适用配置示例：`nvda_g09.json`。

2. WHEN 定义**日线级入口**时 THEN 系统 SHALL 满足：
   - 入口：`scripts/classic_multifactor/run_daily_rebalance.py`。
   - 调度：每日调用 1-2 次（开盘前 / 收盘后 / 人工触发），单次读取 EOD 数据后一次性下单调仓；
     **不启 EVENT_TIMER 分钟循环**。
   - 数据粒度：1d；可直接用 `BacktestingEngine.load_data` 的数据源做一致性。
   - 风控字段：`daily_new_pct` / 组合敞口 / 持仓上限 / 单日最大换手。
   - 配置路由：`configs/classic_multifactor/*.json` 中 `loop_mode == "daily"` 时由此入口消费。
   - 适用配置示例：后续组合型策略 / 多标的日频轮动。

3. WHEN 定义**共享模块**时 THEN 系统 SHALL 要求两个入口共享：
   - `ClassicMultiFactorCtaStrategy` 或其派生类（通过参数区分 intraday/daily 运行模式）。
   - `services/execution_guard/` 全部 gate（幂等 / 对账 / 风控）。
   - `services/strategy/candidate_provider.py` 与 `external.py` / `fusion.py` 旁路。
   - `configs/classic_multifactor/*.json` 的公共字段（symbol、account、risk 基础段）。

4. IF 一个配置同时出现 `loop_mode: intraday` 与日线级独有字段（或反之）THEN 系统 SHALL
   在 `config_schema.py` 启动校验阶段直接拒绝，避免运行时混用。

### 需求 10：scripts/ 顶层入口全面精简

**用户故事：** 作为项目负责人，我希望把 `scripts/` 下的入口收敛到本次重构后真正使用的
几个主线上，把历史多市场（HK / A股）报告、knot demo、一次性探针工具全部清理掉，
避免新贡献者被 70+ 入口误导。

#### 调研基线（不可改动）

`scripts/` 顶层（不含 `classic_multifactor/` 子目录）当前共 **60+ 个 py 入口**，
按主线划分大致如下：

| 类别 | 文件 | 本次重构去留 |
| --- | --- | --- |
| Classic 主线 SIM / Live | `run_us_sim_task.py`, `run_us_sim_close.py`, `run_us_live_task.py`, `run_us_futu_sim_session.py` | **保留**（美股主线，NVDA_G09 依赖路径）|
| Classic 主线健康检查 | `run_healthcheck.py`, `run_futu_sdk_probe.py`, `futu_readonly_snapshot.py`, `utils/probe_futu_opend.py` | **保留**（上线前冒烟）|
| HK / A 股历史多市场 | `run_hk_*.py`（8 个）, `reconcile_hk_live_positions.py`, `run_hk_futu_sim_session.ps1` | **删除**（本次重构只保美股 NVDA_G09，HK / A 股主线若将来需要，从旧分支取回）|
| Knot 独立工具 | `apply_remote_knot_candidates.py`, `prepare_remote_knot_batch_payloads.py`, `run_knot_agent_hk_refresh.py`, `run_intraday_knot_decision.py`, `write_remote_knot_result.py` | **删除**（Knot 路径收敛到 `classic_multifactor/run_llm_research.py` 与 `services/knot_runtime/`，HK 相关 Knot 工具随 HK 主线一起下线）|
| 多市场报告 / 日报 | `run_midday_report.py`, `run_premarket_report.py`, `run_market_recap.py`, `run_multi_market_brief.py`, `run_daily_pipeline.py`, `run_action_distribution.py`, `run_ai_usage_metrics.py`, `run_coverage_metrics.py`, `run_strategy_metrics.py`, `run_strategy_review.py` | **删除**（重构期内不再维护这些多市场报告，Classic 组合简报走 `run_portfolio_brief.py`）|
| 旧回测脚手架 | `run_backtest_scaffold.py`, `run_vnpy_backtest.py`, `run_vnpy_alpha_backtest.py`, `run_vnpy_portfolio_backtest.py`, `run_shared_cash_portfolio_backtest.py` | **删除**（回测唯一入口收敛到 `scripts/classic_multifactor/cta_backtest.py`）|
| 候选 / 数据工具 | `generate_dynamic_candidates.py`, `import_futu_history_to_vnpy.py`, `check_trade_flow_status.py`, `inspect_quote_snapshot.py`, `check_futu_sdk.py`, `reconcile_futu_sim_positions.py`, `run_hk_final_brief.py`, `run_hk_sim_mark.py`, `run_portfolio_brief.py` | **部分保留**：保留 `run_portfolio_brief.py`（美股 + 健康检查使用），其余删除；`import_futu_history_to_vnpy.py` 若 S1 回测仍需要 1m 历史导入则降级为 `services/market_data/` 下内部工具 |
| `utils/` 子目录 | `utils/probe_futu_opend.py` 等 45 项 | **按需保留**，S5 阶段扫一次 dead code，凡是半年内无引用即删 |

#### 验收标准

1. WHEN S0 新分支初始化时 THEN 系统 SHALL 在**首批批量删除**清单里至少包含上表中标记
   "删除"的所有文件，删除后立刻跑 `python -m py_compile scripts/**/*.py` 与
   `scripts/run_healthcheck.py`，失败的单文件从旧分支 checkout 取回并登记"意外依赖"。

2. WHEN S5 清理收尾时 THEN 系统 SHALL 对 `scripts/` 顶层剩余文件做第二轮扫查，
   保留集合必须是下列白名单的子集（可更少，不得更多）：
   - `run_us_sim_task.py`, `run_us_sim_close.py`, `run_us_live_task.py`
   - `run_us_futu_sim_session.py`
   - `run_healthcheck.py`, `run_portfolio_brief.py`
   - `run_futu_sdk_probe.py`, `futu_readonly_snapshot.py`
   - `classic_multifactor/`（整个子目录按需求 8 精简后的结果）
   - `utils/`（按需保留）
   - `README.md`

3. IF 某脚本仅被 Knot / LLM 旁路 demo 使用且 Classic 主线无依赖 THEN 系统 SHALL
   优先删除，需要时回到旧分支取回；Knot 旁路自身脚本只保留 `classic_multifactor/run_llm_research.py`。

4. IF 删除某入口导致 `docs/system_integration_guide.md` 或 README 中的示例命令失效
   THEN 系统 SHALL 在 S5 同步修正文档，避免新用户执行到不存在的命令。

### 需求 11：services/ 模块全面精简

**用户故事：** 作为架构师，我希望 `services/` 下只保留 Classic 美股主线 + Knot 旁路
+ 必要的风控/对账/幂等 红线模块，把已被 vnpy 生态覆盖或只被 HK/多市场使用的自研轮子
一并下线，减少维护面积。

#### 调研基线（不可改动）

`services/` 当前包含 21 个子包。按本次重构目标分类：

| 子包 | 当前用途 | 本次重构去留 | 备注 |
| --- | --- | --- | --- |
| `execution_guard/` | 幂等 / 对账 / pre-check / live gate | **保留**（红线，按需求 5） | |
| `risk_engine/` | live_guard / engine / models | **保留**（红线，按需求 5） | |
| `strategy/` | candidate_provider / classic_adapter / strategy_selector / external_selection / raw_score / timing / market_rules / symbols | **保留**（Classic 业务核心） | |
| `evaluation_hub/` + `knot_runtime/` | Knot/LLM 旁路 | **保留**（按项目规则 3） | |
| `futu_account/` + `futu_opend/` + `futu_sim_trade/` | Futu 适配 | **降级保留**：`futu_account/provider.py` 按需求 2/S2 改为 `OmsEngine` 只读适配器；`FutuSdkClient` 仅保留 auth / 兜底 query；`futu_sim_trade/client.py` 保留给 SIM 会话使用 | |
| `portfolio/risk.py` + `portfolio/summary.py` | 组合敞口 / 简报 | **保留**（`run_portfolio_brief.py` 依赖） | |
| `healthcheck/` | 健康检查 | **保留** | |
| `trading_pipeline/` | `sim_task.py` / `close_task.py` / `live_task.py`(43KB 自写事件循环) | **保留 `sim_task.py` + `close_task.py`，删除 `live_task.py`**：live 路径收敛到 `scripts/classic_multifactor/run_intraday_loop.py` / `run_daily_rebalance.py` 里常驻 MainEngine；`live_task.py` 43KB 的手写订单轮询整体废弃 | 需要 S3a/S3b 完成后再删，避免 `run_us_live_task.py` 过渡期断链 |
| `trade_state/` | `state_machine.py` / `storage.py` / `strategy_state.py` | **降级保留**：按需求 4/S4，`OrderStateStore` 降级为 OmsEngine 事件的只读快照；`strategy_state.py` 若 classic 不再读取则删除 | |
| `common/` | `config_loader.py` / `trading_models.py` | **保留** | |
| `sim_account/` | SimTradingEngine / SimAccountStore | **删除**：当前只被 `run_hk_sim_mark.py`、`reconcile_futu_sim_positions.py`、`run_intraday_knot_decision.py`、`prepare_remote_knot_batch_payloads.py` 这几个**已在需求 10 中被删除的脚本**使用；Classic 美股 SIM 走 `run_us_futu_sim_session.py`（TrdEnv.SIMULATE） | |
| `backtest/` | `engine.py` + `portfolio_engine.py` + `vnpy_*_bridge.py` | **删除**（回测收敛到 `vnpy_ctastrategy.BacktestingEngine`） | 先删 `portfolio_engine.py` 与 `engine.py`；`vnpy_*_bridge.py` 若 `cta_backtest.py` 不引用也删 |
| `candidate_engine/` | `providers/` + `ranker.py` + `storage.py` + `adapters.py` | **删除**：仅被 `run_premarket_report.py` / `check_trade_flow_status.py` / `run_hk_*` 等已删脚本使用；Classic 主线用 `services/strategy/candidate_provider.py` | |
| `watchlist_engine/` | watchlist CRUD | **删除**：仅被 `run_premarket_report.py` 等已删脚本使用 | |
| `scoring_engine/` | `practical_model.py` + `scorer.py` | **删除**：仅被 `run_premarket_report.py` 使用 | |
| `signals/` | `models.py`（仅数据类） | **删除**：仅被 `run_premarket_report.py` 等使用 | |
| `decision_engine/` | `engine.py` + `models.py` | **删除**：仅被 `run_premarket_report.py` / `check_trade_flow_status.py` 使用 | |
| `approval_gate/` | 人工审批 | **删除**：当前只被已下线的 HK / premarket 报告链路使用；Classic 美股实盘审批走硬开关（`VNPY_LIVE_APPROVED=YES`）+ execution_guard，已覆盖 | |
| `reporting/` | `renderers.py` / `schemas.py` / `demo_data.py` | **删除**：仅被 `run_midday_report.py` / `run_premarket_report.py` 使用 | |
| `datahub/` | 仅有 README | **删除**（空壳） | |

#### 验收标准

1. WHEN S0 清理时 THEN 系统 SHALL **直接**删除下列 services 子包（及其 `__pycache__`）：
   - `services/sim_account/`
   - `services/candidate_engine/`
   - `services/watchlist_engine/`
   - `services/scoring_engine/`
   - `services/signals/`
   - `services/decision_engine/`
   - `services/approval_gate/`
   - `services/reporting/`
   - `services/datahub/`
   - `services/backtest/`

2. WHEN S2 完成数据面切换后 THEN 系统 SHALL 删除：
   - `services/trading_pipeline/live_task.py`（43KB 手写轮询路径）。
   - `services/futu_account/` 中不再被 OmsEngine 适配器使用的轮询入口（`accinfo_query` /
     `position_list_query` / `order_list_query` / `deal_list_query` 使用点及相关函数）。

3. WHEN S4 完成订单状态机收敛后 THEN 系统 SHALL 评估：
   - `services/trade_state/strategy_state.py` 若 Classic 不再读写即删除。
   - `services/trade_state/state_machine.py` 若完全被 `OmsEngine.Status` 替代即删除；
     若仍用于独立对账快照则保留但标注"只读快照生成器"。

4. IF 删除某 services 子包后 `python -m py_compile services/**/*.py scripts/**/*.py`
   失败 THEN 系统 SHALL 立刻 checkout 取回并标记"意外依赖"，转为后续阶段分析（严格顺序：
   需求 10 的 scripts 删除必须先于或同批与需求 11 的 services 删除进行，避免 "孤儿 import"）。

5. IF 某 services 子包被 `tests/` 下测试引用 THEN 系统 SHALL 同步删除对应测试文件，
   不保留无意义的绿灯。

### 需求 12：state/runs/ 重置与对账从零重建

**用户故事：** 作为 SRE，我希望本次重构把 `state/runs/` 当作"可丢弃的历史产物"，
保留必要的候选/配置快照，删除历史对账/订单/SIM 账户文件，让新分支以"零状态"启动并
自动在首次运行时重建对账状态。

#### 调研基线（不可改动）

`state/` 当前结构：
- `state/candidates/`：3 个 market 候选池静态 json（a_share / hong_kong / us）。
- `state/watchlists/`：3 个 market watchlist 静态 json。
- `state/runs/`：
  - 当日审批 / drafts / paper_intents 快照（按 market × 日期）。
  - HK 主线产物：`hk_futu_sim_session_*.json`（含 357KB 会话报告）、`hk_sim_*.json`、
    `knot_agent_*_hk.json`、`hk_final_brief.json`、`hk_5w_candidate_refresh.json` 等。
  - US 主线产物：`us_sim_account.json`、`us_sim_task_report.json`、`us_sim_close_report.json`、
    `us_futu_sim_session_*.json`、`us_live_task_report.json`、`classic_multifactor_NVDA_US_live_report.json`、
    `loop_classic_multifactor_NVDA_US_*.jsonl`、`loop_anchor_classic_multifactor_NVDA_US.json`。
  - Classic 回测产物：`runs/classic_multifactor/`（54+ 个扫参 / grid / regime / tuned / walk_forward
    json，含 183KB sweep report 与 62KB single symbol backtest 报告）。
  - 候选输入：`runs/candidate_inputs.json`、`runs/candidate_inputs.dynamic.json`、
    `runs/candidate_inputs/`（子目录）。
  - 订单：`runs/orders/`（sim_* + Futu order id + 16+ 位 hash id 混合）。
  - 其它：`multi_market_brief.json`、`portfolio_brief.json`、`portfolio_us_tech_*.json`、
    `remote_knot_batch_tasks.json`、`shared_cash_portfolio_backtest_report.json`、
    `strategy_metrics.json`、`strategy_selection/*.jsonl`、`vnpy_*_report.json`、
    `vnpy_gateway_events_*.jsonl`、`evaluation_signals.json`、`futu_*.json`。
  - `runs/archive/`：一个历史快照。

#### 验收标准

1. WHEN 定义**保留**清单时 THEN 系统 SHALL 在 S0 清理后继续保留：
   - `state/candidates/us.json`（Classic 美股候选基础，静态配置类）。
   - `state/watchlists/us.json`（同上）。
   - `state/runs/candidate_inputs.json`（主流程输入，若新分支首次运行会再生成则可降级删除）。
   - `state/runs/candidate_inputs/classic_multifactor_NVDA_US.json`（Classic NVDA 输入，明确保留）。
   - `state/runs/archive/`（历史快照目录，作为最终回退凭证）。

2. WHEN 定义**删除**清单时 THEN 系统 SHALL 在 S0 清理时移除下列文件/目录（视为"从零开始"）：
   - 所有 `*hong_kong*` / `hk_*` / `*_hk.json` 等 HK 主线产物。
   - 所有 `*a_share*` 主线产物（approval / drafts / paper_intents）。
   - `state/runs/us_sim_account.json`（SIM 账户由新分支首次运行重建）。
   - `state/runs/us_sim_task_report.json` / `us_sim_close_report.json` /
     `us_futu_sim_session_report.json` / `us_futu_sim_session_state.json` /
     `us_live_task_report.json` / `classic_multifactor_NVDA_US_live_report.json`。
   - `state/runs/loop_classic_multifactor_NVDA_US_*.jsonl` 与 `loop_anchor_*.json`
     （旧 `run_loop.py` 产物，重构后不再产生）。
   - `state/runs/orders/` 目录整体清空（对账从零重建；Classic 美股新分支首单后重新累积）。
   - `state/runs/strategy_selection/*.jsonl`（历史选股轨迹，可重新生成）。
   - `state/runs/classic_multifactor/` 下**历史回测产物**全部删除（54+ 个 json + csv），
     新分支以 `cta_backtest.py` 统一入口重新跑基线。
   - `state/runs/vnpy_*_report.json` / `vnpy_gateway_events_*.jsonl`（旧回测/事件轨迹）。
   - `state/runs/multi_market_brief.json` / `portfolio_us_tech_*.json` / `remote_knot_batch_tasks.json` /
     `shared_cash_portfolio_backtest_report.json` / `strategy_metrics.json` /
     `evaluation_signals.json` / `futu_drafts_*.json` / `futu_sim_position_reconcile.json` /
     `futu_readonly_snapshot.json` / `portfolio_brief.json`。
   - `state/runs/hk_futu_sim_session.pid.json`（PID 文件，确认进程已停后删除）。
   - `state/candidates/a_share.json` / `state/candidates/hong_kong.json`、
     `state/watchlists/a_share.json` / `state/watchlists/hong_kong.json`。

3. WHEN 执行**清理前安全检查**时 THEN 系统 SHALL 要求：
   - 确认当前没有正在运行的 Classic NVDA_G09 live / sim 会话（`ps -ef | grep run_` 无结果，
     或显式停机后再清理）。
   - 清理前把目标清单打印给用户审阅一次，用户回复 `确认清理 state/runs/` 才执行。
   - 清理动作用 `git rm` 而非 `rm`，保留 git 历史可追溯。

4. WHEN 定义**冷启动自举**时 THEN 系统 SHALL 要求新分支首次运行时：
   - `OrderIdempotencyGuard`：从空 index 开始，不读取历史订单。
   - `ReconciliationGuard`：冷启动哨兵生效，Classic NVDA 首次 SIM 运行自动写出新的对账基线。
   - SIM 账户初始化走 `run_us_futu_sim_session.py` 的标准初始化路径（10 万 USD 或配置值）。
   - 若 `candidate_inputs.json` 被删，由 `services/strategy/candidate_provider.py` 首次
     调用时重建；若重建失败则中止启动。

5. IF 删除后冷启动自举失败 THEN 系统 SHALL 先在需求文档中登记缺失依赖文件，
   **不得**通过 git checkout 取回旧文件以掩盖问题，而应补全首次运行的生成逻辑。

6. WHEN 未来需要回溯 THEN 系统 SHALL 依赖：
   - 旧分支 `futu-dev-knot-setup` 的完整 `state/runs/` 历史（最终回退凭证）。
   - 旧分支 tag `classic-pre-vnpy-rewrite-v1`（按需求 6 定义）。
   - `state/runs/archive/`（保留的单一快照）。
