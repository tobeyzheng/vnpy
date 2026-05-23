## 项目操作记录

### 记录规则

- 记录对框架、需求、入口、规则、运行产物结构、阶段边界有明显影响的改动。
- 每条记录尽量包含日期、变更范围、涉及文件和影响摘要。
- 记录保持高层摘要，避免粘贴冗长实现细节。
- 记录中避免出现真实账户金额、完整账号、密钥、令牌等敏感信息。

### 历史记录

- **2026-05-23**：阶段② 多标本地回测 A1 修复 — 引擎注入回测真实池 + 策略 `cond_momentum` 拆分（plan `phase2_multi_backtest`，T15/T16/T17）
  - **代码文件**：[phase2/backtest/portfolio_backtest_engine.py](/projects/vnpy/phase2/backtest/portfolio_backtest_engine.py)（在 `force_live_submit` 翻转之后追加 "A1 pool-injection" 段：用 `self.pool_symbols` 覆盖 `strategy._pool` 并重建 `strategy._state`，仅本地回测语境生效）、[phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py](/projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py)（`cond_momentum` 拆分为 `cond_momentum_bounce`：RSI<35 且单调上升 **或** `cond_momentum_trend`：RSI∈[50,65] 且单调上升；其余进场条件保持 AND）
  - **流程文件**：[.codebuddy/task_list/phase2_multi_backtest.md](/projects/vnpy/.codebuddy/task_list/phase2_multi_backtest.md)（追加 T14/T15/T16/T17 条目与 A1 阶段说明）
  - **运行产物**：[state/runs/phase2_multi_backtest/fixed_pool_5y_baseline/](/projects/vnpy/state/runs/phase2_multi_backtest/fixed_pool_5y_baseline/)（A1 之前基线，trade_count=18 / total_return=-1.02% / 2022-05 之后 NAV 4 年卡死、TSM 全程零成交）vs [state/runs/phase2_multi_backtest/fixed_pool_5y_a1/](/projects/vnpy/state/runs/phase2_multi_backtest/fixed_pool_5y_a1/)（A1 修复后，trade_count=315 / total_return=+26.85% / annualised=+4.89% / max_dd=12.83% / win-loss=66:91 / NVDA·MSFT·AVGO·TSM·TSLA·AMZN 6 标全员成交）
  - **影响摘要**：根因诊断 — futumd 策略文件硬编码 12 标 mega-cap 池以满足 Futu 沙箱单文件零依赖契约，引擎此前只把 `pool_symbols` 注入 `PortfolioRuntime`，从未注入策略，导致引擎装入的 6 标里 TSM 全程被策略忽略；同时 `cond_momentum` 单条要求 "RSI<35（超卖反弹）且 RSI 单调上升"，与 `cond_trend` 的 "趋势向上" AND 关系几乎不可能同时成立，2022-05 之后大熊市 + 牛市单边窗口里持续不开仓。本轮采用最小修复：① 引擎在 `strategy.initialize()` 之后注入 `strategy._pool / _state`（**仅本地回测路径生效，futu 沙箱契约 / 策略文件硬编码池 / `LIVE_SUBMIT=False` 全部保持不变**，futu 平台依旧看到 12 标版本）；② 策略 `cond_momentum` 改为 bounce ∨ trend OR，保留方向过滤（`rsi >= last_rsi`）。验证链路：`python3 phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py --check` smoke 通过（pool 仍为 12，符合 futu 契约）+ `phase2/backtest/portfolio_backtest_engine.py` `py_compile` 通过 + `phase2/strategy/tests/` 75 passed（73→75 不变 → 75，对照单测全部仍然过）+ 重跑固定池 5 年回测 NAV 全程波动正常。本轮**不连 OpenD/Futu/任何远端服务、不发任何 SIM/REAL 订单、未改写账户/订单/风控状态**，仅在本地数据库读 + 本地 `state/runs/` 写。

- **2026-05-21**：阶段② 多标本地回测引擎引入 `force_live_submit` 开关 + 12 标 1 年 smoke 完成（plan `phase2_multi_backtest`，T12/T13）
  - **代码文件**：[phase2/backtest/portfolio_backtest_engine.py](/projects/vnpy/phase2/backtest/portfolio_backtest_engine.py)、[phase2/runners/run_phase2_multi_backtest.py](/projects/vnpy/phase2/runners/run_phase2_multi_backtest.py)
  - **测试文件**：[phase2/strategy/tests/test_portfolio_backtest_engine.py](/projects/vnpy/phase2/strategy/tests/test_portfolio_backtest_engine.py)（新增 `LiveSubmitOverrideTests` 2 项，对照默认翻转能下单 vs `--respect-live-submit` 零下单）
  - **流程文件**：[.codebuddy/task_list/phase2_multi_backtest.md](/projects/vnpy/.codebuddy/task_list/phase2_multi_backtest.md)（T12/T13 状态更新 + smoke 摘要落地）
  - **文档文件**：[docs/system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md) 阶段② 多标本地回测小节同步 `force_live_submit` 行为与 `--respect-live-submit` 用法
  - **运行产物**：[state/runs/phase2_multi_backtest/smoke_2025_2026_v2/](/projects/vnpy/state/runs/phase2_multi_backtest/smoke_2025_2026_v2/) — 12 标 / 251 个交易日 / trade_count=23 / total_return +1.34% / max_drawdown 3.59% / 末日全部 0 仓
  - **影响摘要**：futumd 策略 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` 零改动；引擎在 `strategy.initialize()` 之后默认翻转 `strategy.LIVE_SUBMIT=True`，使本地回测能观测到 `place_limit` intent。该翻转**仅作用于本地回测语境**：adapter 的 `place_limit` 永远只入内存队列、不连 OpenD/Futu/任何远端服务、不发任何 SIM/REAL 订单；策略源码上传 Futu 平台后 `LIVE_SUBMIT` 仍以平台拿到的源码（False）为准。CLI 新增 `--respect-live-submit` 开关用于回放 dry-run alert 分支。phase2 自动化测试 73 → 75 全过。

- **2026-05-20**：阶段② 多标本地回测落地（plan `phase2_multi_backtest`）
  - **代码文件**：[phase2/backtest/__init__.py](/projects/vnpy/phase2/backtest/__init__.py)、[phase2/backtest/futumd_strategy_adapter.py](/projects/vnpy/phase2/backtest/futumd_strategy_adapter.py)、[phase2/backtest/portfolio_backtest_engine.py](/projects/vnpy/phase2/backtest/portfolio_backtest_engine.py)、[phase2/runners/run_phase2_multi_backtest.py](/projects/vnpy/phase2/runners/run_phase2_multi_backtest.py)
  - **测试文件**：[phase2/strategy/tests/test_futumd_strategy_adapter.py](/projects/vnpy/phase2/strategy/tests/test_futumd_strategy_adapter.py)（17 项）、[phase2/strategy/tests/test_portfolio_backtest_engine.py](/projects/vnpy/phase2/strategy/tests/test_portfolio_backtest_engine.py)（6 项）
  - **流程文件**：[.codebuddy/plan/phase2_multi_backtest/README.md](/projects/vnpy/.codebuddy/plan/phase2_multi_backtest/README.md)、[.codebuddy/task_list/phase2_multi_backtest.md](/projects/vnpy/.codebuddy/task_list/phase2_multi_backtest.md)
  - **文档文件**：[docs/system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md) 新增"阶段② 多标本地回测入口"小节
  - **影响摘要**：新增独立子包 `phase2/backtest/` 提供按 symbol 分桶的运行时（`PortfolioRuntime`）+ futumd DSL 注入层（`build_futumd_namespace`）+ 多标日期 union 驱动引擎（`PortfolioBacktestEngine`，次日开盘撮合避免未来函数）；新增 CLI `phase2/runners/run_phase2_multi_backtest.py`，产物落到 `state/runs/phase2_multi_backtest/<run_id>/`（`equity_curve.csv` / `positions_daily.csv` / `trade_ledger.csv` / `summary.json`）。**futumd 策略源码 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` 零改动**；与 `tmp/` 完全解耦不 import 任何 `tmp/*` 模块；不连 OpenD / 不下任何 SIM/REAL 单 / `LIVE_SUBMIT` 保持 False。phase2 自动化测试 50 → 73 全过（17 adapter + 6 engine 新增）。12 标 1 年真回测 smoke 留待用户单独"确认执行"再触发，未在本轮产生 `state/runs/` 写入。

- **2026-05-20**：新增"美股多标的量化策略"研究目录（仅产研究/设计文档，不含可执行代码）
  - **文档文件**：[docs/research/us_multi_symbol_quant/00_index.md](/projects/vnpy/docs/research/us_multi_symbol_quant/00_index.md)、[01_indicator_research.md](/projects/vnpy/docs/research/us_multi_symbol_quant/01_indicator_research.md)、[02_futu_multi_symbol_capability.md](/projects/vnpy/docs/research/us_multi_symbol_quant/02_futu_multi_symbol_capability.md)、[03_strategy_design.md](/projects/vnpy/docs/research/us_multi_symbol_quant/03_strategy_design.md)
  - **流程文件**：[.codebuddy/plan/us_multi_symbol_quant/](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/)（requirements / design / tasks）、[.codebuddy/task_list/us_multi_symbol_quant.md](/projects/vnpy/.codebuddy/task_list/us_multi_symbol_quant.md)
  - **影响摘要**：新增产物结构 `docs/research/us_multi_symbol_quant/`，含 4 份研究/设计文档（汇总入口 + 指标调研 + Futu 多标的能力评估 + 多标的策略方案），全部为研究文档**不含可执行代码**；所有 Futu API 名与行号经 `grep_search` 二次校验，全文使用脱敏占位符（NAV/pool_budget_pct）不含真实金额；本轮**未修改任何现有策略源码**（`us_nvda_1d_strategy_multifactor.py` / `strategy_classic_multifactor.py` / `run_local_backtest.py` 等保持不变），未运行回测、未连 OpenD、未下单、未改写 `state/runs/`；落地（阶段 ② 固定股票池 / 阶段 ③ 横截面排序）需走独立 `.codebuddy/plan/` 流程，并在落地阶段补全 [system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md) 顶层反向引用。

- **2026-05-12**：Knot research bundle 为 HK / US 持仓 review 新增中午 `12:00` 调度
  - **代码文件**：[run_knot_research_bundle.py](/projects/vnpy/scripts/quant_workflow/run_knot_research_bundle.py)、[test_knot_research_bundle.py](/projects/vnpy/tests/test_knot_research_bundle.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：`run_knot_research_bundle.py` 的 `--schedule-workdays` 现在除保留 HK / US 开盘前 `09:00` 的持仓 review 外，还会在各自市场本地时区工作日 `12:00` 再追加 1 次只读持仓审阅；本轮未改动四维选股的触发时间，也未新增任何下单或账户写操作，仅扩展调度时点并补充对应测试与系统集成说明。

- **2026-05-12**：新增 A 股四维选股入口，并接入 Knot research bundle 的盘前工作日调度
  - **代码文件**：[knot_pick_helpers.py](/projects/vnpy/services/strategy/knot_pick_helpers.py)、[symbols.py](/projects/vnpy/services/strategy/symbols.py)、[run_knot_4dim_picks_cn.py](/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_cn.py)、[run_knot_research_bundle.py](/projects/vnpy/scripts/quant_workflow/run_knot_research_bundle.py)、[test_knot_pick_helpers.py](/projects/vnpy/tests/test_knot_pick_helpers.py)、[test_knot_research_bundle.py](/projects/vnpy/tests/test_knot_research_bundle.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：Knot 四维研究入口现在新增 `china`（A 股）市场，支持 `*.SH` / `*.SZ` 代码标准化、跨市场后缀过滤与 `CN` 紧凑文本标签；新增 [run_knot_4dim_picks_cn.py](/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_cn.py) 并默认把结构化输出写到 `log/YYYYMMDDHH/knot_4dim_cn.json`。`run_knot_research_bundle.py` 的立即执行顺序调整为 `HK -> CN -> US -> holdings review`，同时 `--schedule-workdays` 新增 `Asia/Shanghai` 工作日 `09:00` 的 A 股盘前选股触发；本轮未新增 A 股 holdings review，仍保持仅 HK / US 开盘前做只读持仓审阅。

- **2026-05-12**：Knot research bundle 新增按工作日 + 市场时区调度，并将持仓 review 默认锁定到 `REAL` 只读环境
  - **代码文件**：[run_knot_research_bundle.py](/projects/vnpy/scripts/quant_workflow/run_knot_research_bundle.py)、[run_holdings_knot_review.py](/projects/vnpy/scripts/quant_workflow/run_holdings_knot_review.py)、[test_knot_research_bundle.py](/projects/vnpy/tests/test_knot_research_bundle.py)、[test_run_holdings_knot_review.py](/projects/vnpy/tests/test_run_holdings_knot_review.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：`run_knot_research_bundle.py` 在保留原先顺序立即执行模式的同时，新增 `--schedule-workdays` 常驻调度能力，按 `Asia/Hong_Kong` 与 `America/New_York` 的工作日 `09:00` 分别触发 HK / US 四维选股，并各执行 1 次持仓 review；新增轮询、心跳和补跑窗口参数以降低"静默等待像假死"的排障成本。`run_holdings_knot_review.py` 新增 `--trd-env REAL|SIMULATE` 与 `--live-strict` 参数，bundle 默认把持仓审阅锁定到 `REAL` 环境的只读账户查询，但仍不提交任何订单或改写交易状态。

- **2026-05-12**：Knot 研究类入口改为按小时写入 `log/`，并新增顺序批量执行入口
  - **代码文件**：[knot_pick_helpers.py](/projects/vnpy/services/strategy/knot_pick_helpers.py)、[run_knot_4dim_picks_hk.py](/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_hk.py)、[run_knot_4dim_picks_us.py](/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_us.py)、[run_holdings_knot_review.py](/projects/vnpy/scripts/quant_workflow/run_holdings_knot_review.py)、[run_knot_research_bundle.py](/projects/vnpy/scripts/quant_workflow/run_knot_research_bundle.py)、[test_knot_pick_helpers.py](/projects/vnpy/tests/test_knot_pick_helpers.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：3 个 Knot 研究入口默认不再把结构化 JSON 写到 `state/runs/`，而是统一写到 `log/YYYYMMDDHH/` 下的同名文件，方便按小时归档研究输出；新增 `run_knot_research_bundle.py` 作为顺序编排入口，按 HK 四维选股 → US 四维选股 → 持仓方向审阅 的顺序执行，并默认让 3 份产物共享同一个小时目录。显式传入 `--output` / `--output-dir` 时仍可覆盖默认路径。

- **2026-05-11**：修复 live intraday 路径下 1m bar 永远收不到 + 心跳字段误报
  - **代码文件**：[strategy.py](/projects/vnpy/scripts/classic_multifactor/strategy.py)、[run_intraday_loop.py](/projects/vnpy/scripts/classic_multifactor/run_intraday_loop.py)
  - **影响摘要**：`ClassicMultiFactorCtaStrategy` 之前 `on_tick` 为空且无 `BarGenerator`，导致 vnpy CtaEngine 在 live 模式下永远不会触发 `on_bar`（FutuGateway 仅订阅 `QUOTE/ORDER_BOOK`，不订 K_1M，也不下发 1m bar），策略恒卡在 `last_signal=warmup`、`bars_seen=0`。本次按 vnpy 官方示例新增 `self.bg = BarGenerator(self.on_bar)` 并在 `on_tick` 中调 `self.bg.update_tick(tick)`，让 live tick 本地合成 1m bar 推给 `on_bar`；回测路径不经 `on_tick`（vnpy `BacktestingEngine` 直接调 `strategy.on_bar`），行为零变化。同时清理 intraday 心跳里的 `gateway_connected` 字段（`BaseGateway` 无此属性，恒为 False 形成误报），替换为 `quote_ctx_attached`（FutuGateway 是否仍持有 `OpenQuoteContext`）+ `gateway_data_flowing`（本会话是否已收到至少 1 根 bar）两个语义正确的字段，并同步刷新 heartbeat 日志格式。

- **2026-05-11**：候选输入产物按市场拆分为 HK / US 独立文件，从根本消除并发覆盖
  - **代码文件**：[candidate_preparation.py](/projects/vnpy/services/strategy/candidate_preparation.py)、[candidate_provider.py](/projects/vnpy/services/strategy/candidate_provider.py)、[candidate_generation.py](/projects/vnpy/services/strategy/candidate_generation.py)、[workflow_service.py](/projects/vnpy/scripts/quant_workflow/workflow_service.py)、[test_candidate_provider.py](/projects/vnpy/tests/test_candidate_provider.py)、[test_candidate_preparation_market_scope.py](/projects/vnpy/tests/test_candidate_preparation_market_scope.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[candidate_input_example.md](/projects/vnpy/docs/protocols/candidate_input_example.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：候选输入产物拆分为 per-market 独立文件 `state/runs/candidate_inputs.dynamic.{hong_kong,us}.json` / `state/runs/candidate_inputs.static.{hong_kong,us}.json` / `state/runs/candidate_inputs.prepare.report.{hong_kong,us}.json`，HK 与 US 的 prepare 流程互不相交，可并发刷新而不再相互覆盖；`CandidateInputPreparationService.prepare_market()` 现在每个 market 写自己那份 dynamic + 报告，不再触碰其它市场的文件；legacy `prepare()` 同步改为按市场分别写 dynamic + static + 报告，并仍写一份聚合 `candidate_inputs.prepare.report.json` 作为 back-compat 摘要（含新字段 `per_market_report_paths` / `per_market_dynamic_paths`，`written_targets` 命名升级为 `dynamic_{market}` / `static_{market}`）。`UnifiedCandidateProvider` 改为按 market 探测新文件，仅当 per-market 文件全缺时回退读老的 `candidate_inputs.dynamic.json` / `candidate_inputs.json`（兼容窗口），合并规则保持 `(market, symbol)` 字段级 dynamic 覆盖 static。`workflow_service.py` 的 readiness 探测也扩为按市场任一文件存在即为就绪。

- **2026-05-11**：候选 universe 升级为 `get_stock_filter` 条件筛选，并加固 enrichment 容错与零结果守护
  - **代码文件**：[universe.py](/projects/vnpy/services/strategy/universe.py)、[candidate_enrichment.py](/projects/vnpy/services/strategy/candidate_enrichment.py)、[candidate_preparation.py](/projects/vnpy/services/strategy/candidate_preparation.py)、[run_prepare_candidate_inputs.py](/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py)、[test_market_universe_provider.py](/projects/vnpy/tests/test_market_universe_provider.py)、[test_candidate_scoring.py](/projects/vnpy/tests/test_candidate_scoring.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：`FutuMarketUniverseProvider.list_symbols` 默认改走 `OpenQuoteContext.get_stock_filter`，按市值降序 + 流动性 / 价格阈值分页拉取目标市场的高质量短名单；新增 `--universe-preset large_cap|momentum_cta|none` 选项，`large_cap` 给 HK / US 各自配置市值、价格、成交额阈值，`momentum_cta` 在此基础上叠加 N 日涨幅 / 量比等技术过滤（缺失字段时自动忽略），`none` 与连接的 SDK 不支持 `get_stock_filter` 时都会自动降级回 `get_stock_basicinfo`；`--universe-limit` 默认从 800 下调到 200，对齐 OpenD 单页上限。`CandidateMarketDataService.enrich_rows` 新增**分批 + 二分降级**容错：每批不超过 200 个 code，单批失败时递归二分重试，单 symbol 持续失败会被隔离为 `invalid_symbol`，整体失败时返回 `status=error`。`CandidateInputPreparationService._prepare_single_market` 增加**零 enrichment 守护**：当 `score_first` 路径下 snapshot 富化整体失败或匹配为 0 时，本次刷新写 `kept_count=0` + 警告，**不再**用未富化的字母序结果覆盖动态池。CLI / report schema 中 `market_runs[*]` 新增 `universe_preset` 字段以追溯执行路径。

- **2026-05-11**：候选输入准备升级为按市场独立刷新动态池，默认 `knot_first` 失败降级 `score_first`
  - **代码文件**：[candidate_preparation.py](/projects/vnpy/services/strategy/candidate_preparation.py)、[candidate_seed.py](/projects/vnpy/services/strategy/candidate_seed.py)、[universe.py](/projects/vnpy/services/strategy/universe.py)、[run_prepare_candidate_inputs.py](/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py)、[test_candidate_seed_service.py](/projects/vnpy/tests/test_candidate_seed_service.py)、[test_market_universe_provider.py](/projects/vnpy/tests/test_market_universe_provider.py)、[test_candidate_preparation_market_scope.py](/projects/vnpy/tests/test_candidate_preparation_market_scope.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：新增 `KnotCandidateSeedService`（远端 Knot agent 直接生成候选，复用 `OpenAICompatibleClient` 协议、按市场过滤并标准化为 candidate schema）与 `FutuMarketUniverseProvider`（基于 `OpenQuoteContext.get_stock_basicinfo` 拉 HK/US 全市场标的并归一化为 dotted symbol）；`CandidateInputPreparationService` 新增 `prepare_market(market, strategy=knot_first|score_first|merge_existing, top_n, knot_target_count, dry_run, ...)` 入口，按市场单独刷新 `state/runs/candidate_inputs.dynamic.json` 中目标市场的行，其他市场已有的行保持不变；当 `knot_first` 远端不可用时自动降级到 `score_first`，并在 `candidate_inputs.prepare.report.json`（升级到 `candidate_prepare_report_v3`）记录 `target_markets`、`market_runs[*].strategy_used`、`knot_status`、`universe_size`、`kept_count`、`top_symbols`；CLI [run_prepare_candidate_inputs.py](/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py) 默认走新流程，老 `prepare()` 调用通过 `--legacy` 显式保留。静态池本轮不动。

- **2026-05-11**：升级候选流动性打分公式并统一评分层 / 框架层口径
  - **代码文件**：[candidate_scoring.py](/projects/vnpy/services/strategy/candidate_scoring.py)、[candidate_framework.py](/projects/vnpy/services/evaluation_hub/candidate_framework.py)、[candidate_enrichment.py](/projects/vnpy/services/strategy/candidate_enrichment.py)、[test_candidate_scoring.py](/projects/vnpy/tests/test_candidate_scoring.py)、[test_strategy_engine.py](/projects/vnpy/tests/test_strategy_engine.py)
  - **文档文件**：[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：`CandidateScoringService` 的 `liquidity_score` / `flow_score` 已从简单 `turnover_ratio / 2` 升级为更接近业界的多因子启发式模型：优先考虑绝对成交额，再叠加换手率、点差/深度代理和文本低流动性惩罚；`candidate_framework` 现直接复用同一套共享 helper，避免候选评分层与 workflow 观察层各自维护分叉的流动性阈值表，从而降低美股/港股大票被误判为 `thin_liquidity` 的概率。

- **2026-05-11**：统一 HK / US 交易时段默认口径到 `market_rules`
  - **代码文件**：[market_rules.py](/projects/vnpy/services/strategy/market_rules.py)、[_base_runner.py](/projects/vnpy/scripts/classic_multifactor/_base_runner.py)、[run_intraday_loop.py](/projects/vnpy/scripts/classic_multifactor/run_intraday_loop.py)、[run_daily_rebalance.py](/projects/vnpy/scripts/classic_multifactor/run_daily_rebalance.py)、[strategy.py](/projects/vnpy/scripts/classic_multifactor/strategy.py)、[live_task.py](/projects/vnpy/services/trading_pipeline/live_task.py)、[run_us_futu_sim_session.py](/projects/vnpy/scripts/run_us_futu_sim_session.py)、[market_watchlist_trader.py](/projects/vnpy/examples/futu_trader/market_watchlist_trader.py)、[test_intraday_loop_pipeline.py](/projects/vnpy/tests/test_intraday_loop_pipeline.py)、[test_daily_rebalance_runner.py](/projects/vnpy/tests/test_daily_rebalance_runner.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：项目内分散在 classic runner、CTA 策略、legacy live task、US Futu session 和示例脚本里的 HK / US 默认时区、regular session、`no_new_entry_after` 与 `rebalance_time` 口径已开始统一到 `services/strategy/market_rules.py`；当 CLI 或 config 未显式覆盖时，主线入口会按 symbol 所属市场自动推导默认交易所时区与 session 边界，避免港股任务误沿用 `America/New_York` 等硬编码常量。

- **2026-05-11**：为 classic intraday runner 增加分钟任务关键点排障日志
  - **代码文件**：[run_intraday_loop.py](/projects/vnpy/scripts/classic_multifactor/run_intraday_loop.py)、[test_intraday_loop_pipeline.py](/projects/vnpy/tests/test_intraday_loop_pipeline.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：分钟级 runner 现在会在 live `on_bar` 的入口/出口输出 `intraday debug checkpoint`，并在主循环内每 60 秒输出 `intraday runtime heartbeat`，补充 `bars_seen`、`last_bar_time`、`seconds_since_last_bar`、`bars_cached`、审批/拦截累计值等关键上下文；这样当任务重启后再次出现"进程存活但日志静默"时，可以更快区分是行情 bar 根本没进策略，还是策略内部某个早退/决策阶段没有继续推进。

- **2026-05-11**：修复 classic intraday runner 的分钟决策点日志边界判断
  - **代码文件**：[run_intraday_loop.py](/projects/vnpy/scripts/classic_multifactor/run_intraday_loop.py)、[test_intraday_loop_pipeline.py](/projects/vnpy/tests/test_intraday_loop_pipeline.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：分钟级 runner 的 `intraday bar result` 决策点判断现改为基于当前 `bar.datetime` 的分钟边界（如 `15m` 策略对应 `:00/:15/:30/:45`），不再依赖会被截断的 `bars` 缓冲区长度取模；这样在 warmup 完成且 bar 缓冲区封顶后，后续 live 决策点日志仍会持续输出，便于继续观测分钟任务是否正常接收行情与触发策略评估。

- **2026-05-11**：修复 classic strategy warmup 历史加载语义，避免分钟任务误回放数百天历史
  - **代码文件**：[strategy.py](/projects/vnpy/scripts/classic_multifactor/strategy.py)、[_base_runner.py](/projects/vnpy/scripts/classic_multifactor/_base_runner.py)、[cta_backtest.py](/projects/vnpy/scripts/classic_multifactor/cta_backtest.py)、[run_vnpy_cta_backtest.py](/projects/vnpy/scripts/classic_multifactor/run_vnpy_cta_backtest.py)、[test_intraday_loop_pipeline.py](/projects/vnpy/tests/test_intraday_loop_pipeline.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：classic strategy 现在会根据 `data_interval` 把 warmup 所需 bar 数显式换算为 vn.py `load_bar(days=...)` 需要的自然日天数；`1m` 分钟策略不再把 `480` 根预热 bar 误当成 `480` 天历史回放，从而显著缩短启动预热阶段并避免长时间停留在 `last_signal=warmup`。共享 runner 与回测入口也会把 `interval` 透传给策略设置，确保实盘/模拟/回测的 warmup 行为一致。

- **2026-05-11**：收敛 classic intraday runner 的分钟日志口径，并补充审批/事件产物说明
  - **代码文件**：[run_intraday_loop.py](/projects/vnpy/scripts/classic_multifactor/run_intraday_loop.py)、[test_intraday_loop_pipeline.py](/projects/vnpy/tests/test_intraday_loop_pipeline.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：分钟级 runner 现在不会再为 warmup 历史 bar 或普通非决策 `1m` bar 输出 `intraday bar result`；仅在真实信号评估边界、审批通过、风控拦截、异常或订单状态变化时记录关键日志，减少启动与运行期刷屏。同时补充了 `state/runs/<execution_env>/events.jsonl` 与 `orders/*.json` 的职责说明，明确前者用于审批/提交事件审计，后者用于正式订单状态持久化、跨重启幂等与 OMS 回报关联。

- **2026-05-11**：增强 classic intraday/daily runner 的运行可观测性
  - **代码文件**：[run_intraday_loop.py](/projects/vnpy/scripts/classic_multifactor/run_intraday_loop.py)、[run_daily_rebalance.py](/projects/vnpy/scripts/classic_multifactor/run_daily_rebalance.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：分钟级 runner 现在会为每根进入策略的 bar 输出 `intraday bar result` 摘要日志，覆盖 `approved_dry_run`、`approved_or_submitted`、`blocked`、`no_action` 等本轮结果，即使没有实际下单也可追踪策略动作；日级 runner 在等待 `rebalance_time` 期间会先输出启动等待日志，并每 10 分钟输出一次心跳日志，降低"进程活着但日志空白"带来的排障歧义。

- **2026-05-11**：补齐 dual-run / preflight 工具对新执行环境目录布局的兼容，并同步修正文档说明
  - **代码文件**：[diff_dual_run.py](/projects/vnpy/scripts/diff_dual_run.py)、[dual_run_preflight.py](/projects/vnpy/scripts/dual_run_preflight.py)、[execution_pipeline.py](/projects/vnpy/scripts/classic_multifactor/execution_pipeline.py)、[oms_recorder.py](/projects/vnpy/services/trade_state/oms_recorder.py)、[test_dual_run_layout_compat.py](/projects/vnpy/tests/test_dual_run_layout_compat.py)
  - **文档文件**：[USAGE_GUIDE.md](/projects/vnpy/scripts/classic_multifactor/USAGE_GUIDE.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：`diff_dual_run` 与 `dual_run_preflight` 现在会优先扫描 `state/runs/dry_run|futu_sim|futu_real/` 下的 `orders/` 与 `events.jsonl`，并保留对旧 `state/runs/orders/` 与根级 `events.jsonl` 的回退兼容；classic 主链路与 OMS 注释、使用指南也已同步更新到分环境目录表述，减少后续人工排查仍按旧路径理解运行产物的混淆。

- **2026-05-11**：隔离 classic mainline 的 warmup 订单痕迹，并按执行环境拆分订单状态目录
  - **代码文件**：[trading_models.py](/projects/vnpy/services/common/trading_models.py)、[state_machine.py](/projects/vnpy/services/trade_state/state_machine.py)、[strategy.py](/projects/vnpy/scripts/classic_multifactor/strategy.py)、[_base_runner.py](/projects/vnpy/scripts/classic_multifactor/_base_runner.py)、[execution_pipeline.py](/projects/vnpy/scripts/classic_multifactor/execution_pipeline.py)、[engine.py](/projects/vnpy/services/sim_account/engine.py)、[run_us_futu_sim_session.py](/projects/vnpy/scripts/run_us_futu_sim_session.py)、[executor.py](/projects/vnpy/execution/vnpy_bridge/executor.py)、[test_order_state_machine.py](/projects/vnpy/tests/test_order_state_machine.py)、[test_intraday_loop_pipeline.py](/projects/vnpy/tests/test_intraday_loop_pipeline.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：`OrderIntent` / `OrderState` 现显式记录 `execution_channel`、`execution_env`、`source_phase` 与 `submitted_to_broker`；classic strategy 在 `on_init()` 的 `load_bar()` warmup 阶段不再通过 execution hook 落正式订单状态，避免历史分钟线回放生成大量 dry-run 订单痕迹；classic mainline 与 US Futu SIM session 的订单状态/事件日志按 `dry_run`、`futu_sim`、`futu_real` 拆分到 `state/runs/<execution_env>/` 子目录，减少模拟与真实订单混放造成的审计和恢复混淆。

- **2026-05-11**：将 `quant_workflow` 与 evaluation hub 默认语义从 `beginner_*` 迁移到交易导向命名
  - **代码文件**：[candidate_framework.py](/projects/vnpy/services/evaluation_hub/candidate_framework.py)、[beginner_candidate_selector.py](/projects/vnpy/services/evaluation_hub/beginner_candidate_selector.py)、[models.py](/projects/vnpy/services/evaluation_hub/models.py)、[plan_generator.py](/projects/vnpy/services/evaluation_hub/plan_generator.py)、[doc_renderer.py](/projects/vnpy/services/evaluation_hub/doc_renderer.py)、[readiness_gate.py](/projects/vnpy/services/evaluation_hub/readiness_gate.py)、[workflow_service.py](/projects/vnpy/scripts/quant_workflow/workflow_service.py)、[run_quant_workflow.py](/projects/vnpy/scripts/quant_workflow/run_quant_workflow.py)、[__init__.py](/projects/vnpy/services/evaluation_hub/__init__.py)、[beginner_research.py](/projects/vnpy/services/evaluation_hub/beginner_research.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)
  - **文档/进度文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)、[beginner_quant_planning.md](/projects/vnpy/.codebuddy/task_list/beginner_quant_planning.md)
  - **影响摘要**：workflow 默认名、artifact slug 与 CLI preset 已切到 `quant_trading*`；候选主 bucket 语义已切到 `priority_trade` / `active_watch` / `research_queue` / `exclude`，workflow/readiness/backtest 资格判断改为消费 `bucket`、`backtest_ready`、`hard_risk_flags` 与 `manual_review_required` 等结构化字段；计划生成器、文档渲染器和导出接口已新增 `Trading*` 类名并保留旧别名兼容，下游可逐步从 `Beginner*` 迁移到交易语义调用。

- **2026-05-11**：为 `quant_workflow` 的 `backtest` 阶段补齐显式可执行的真实回测与自动扫参模式
  - **代码文件**：[workflow_service.py](/projects/vnpy/scripts/quant_workflow/workflow_service.py)、[run_quant_workflow.py](/projects/vnpy/scripts/quant_workflow/run_quant_workflow.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)、[quant-workflow-refactor.md](/projects/vnpy/.codebuddy/task_list/quant-workflow-refactor.md)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：`quant_workflow` 的 `backtest` 阶段默认仍支持复用本地 `vnpy_cta_backtest_report.json` / `*sweep*.json`，但在显式开启 `auto_execute_backtests` 时，会改为为 observation target 自动拉取或复用历史 bars、执行真实 vn.py CTA backtest、执行 `bf|ga` 参数搜索，并把 per-symbol 报告写回 `state/runs/classic_multifactor/`；同时 `healthcheck` 会在该模式下允许"缺本地 backtest 报告先继续，由 backtest 阶段补生成"，artifact 也会记录 `execution_mode`、执行区间、参数搜索模式与新产物路径，便于后续 readiness 和人工复核。

- **2026-05-11**：修复 `quant_workflow` 的港股市场别名过滤与 HK backtest target 规则
  - **代码文件**：[workflow_service.py](/projects/vnpy/scripts/quant_workflow/workflow_service.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：`quant_workflow` 当前会先把 `preferred_market` 中的 `hk` / `hongkong` 统一归一化为 `hong_kong`，避免港股候选在 market 精确匹配阶段被整体过滤为空；同时 `candidate_framework` artifact 现在会额外记录 `backtest_target_eligible` 与 `backtest_target_reason`，并允许港股 `validate_only` 但已被规则层赋予 `daily|minute` cadence 的 observation 进入 evidence-only `backtest` 证据整理，便于先复用本地回测产物，再由后续 readiness 决定是否升级。

- **2026-05-11**：`quant_workflow` 重构为五阶段主链路
  - **代码文件**：[workflow_service.py](/projects/vnpy/scripts/quant_workflow/workflow_service.py)、[run_quant_workflow.py](/projects/vnpy/scripts/quant_workflow/run_quant_workflow.py)、[candidate_framework.py](/projects/vnpy/services/evaluation_hub/candidate_framework.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)、[quant-workflow-refactor.md](/projects/vnpy/.codebuddy/task_list/quant-workflow-refactor.md)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：`quant_workflow` 现收敛为 `candidate_prepare`（可选）→ `healthcheck` → `candidate_framework` → `backtest` → `readiness` 五阶段；旧的 `preflight`、`research`、`planning`、`execution_boundary` 默认不再执行。统一 `healthcheck` 会按 `simulation|live` 检查不同本地证据；`candidate_framework` 现在会为观察标输出 `daily|minute|needs_review` 交易级别；`backtest` 阶段会复用本地回测/扫参产物整理最佳参数与绩效证据；`readiness` 则按任务类型执行证据门禁。CLI 预设、artifact 路径、集成文档和测试已在同轮同步更新，`tests/test_beginner_quant_workflow.py` 当前回归为 26 项通过。

- **2026-05-11**：补齐 HK 顶层包装入口的 `futu-market` 默认值
  - **代码文件**：[run_hk_sim_task.py](/projects/vnpy/scripts/run_hk_sim_task.py)、[run_hk_futu_sim_session.py](/projects/vnpy/scripts/run_hk_futu_sim_session.py)、[run_hk_live_task.py](/projects/vnpy/scripts/run_hk_live_task.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：3 个 HK 顶层包装入口现在都会在转发到 `scripts/classic_multifactor/run_intraday_loop.py` 时默认注入 `--futu-market HK`（除非调用方显式覆盖），从而让港股 SIM / Futu SIM / live 会话在正确市场上下文里做合约查询与订阅，避免"连接成功、策略已启动，但港股合约找不到"的错误市场默认值问题。

- **2026-05-11**：修复 HK Futu SIM 会话的港股交易所后缀规范化
  - **代码文件**：[ _base_runner.py ](/projects/vnpy/scripts/classic_multifactor/_base_runner.py)、[test_daily_rebalance_runner.py](/projects/vnpy/tests/test_daily_rebalance_runner.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：共享 classic runner 的 `map_vt_symbol()` 现在会把港股 classic config 中研究侧常用的 `*.HK` 自动规范化为 vn.py 会话创建所需的 `*.SEHK`，从而让 [run_hk_futu_sim_session.py](/projects/vnpy/scripts/run_hk_futu_sim_session.py) 这类 HK SIM/live 包装入口在沿用 `00700.HK` 配置时也能正确创建策略实例；同时补充了回归测试与系统集成说明，减少"回测可跑但会话启动失败"的符号后缀偏差。

- **2026-05-10**：补齐 HK 顶层入口、readiness 证据链与候选池可追溯性
  - **代码文件**：[run_hk_sim_task.py](/projects/vnpy/scripts/run_hk_sim_task.py)、[run_hk_futu_sim_session.py](/projects/vnpy/scripts/run_hk_futu_sim_session.py)、[run_hk_live_task.py](/projects/vnpy/scripts/run_hk_live_task.py)、[tencent_hk_g01.json](/projects/vnpy/configs/classic_multifactor/tencent_hk_g01.json)、[capability_registry.py](/projects/vnpy/services/evaluation_hub/capability_registry.py)、[readiness_gate.py](/projects/vnpy/services/evaluation_hub/readiness_gate.py)、[workflow_service.py](/projects/vnpy/scripts/quant_workflow/workflow_service.py)、[candidate_provider.py](/projects/vnpy/services/strategy/candidate_provider.py)、[candidate_preparation.py](/projects/vnpy/services/strategy/candidate_preparation.py)、[candidate_generation.py](/projects/vnpy/services/strategy/candidate_generation.py)、[candidate_enrichment.py](/projects/vnpy/services/strategy/candidate_enrichment.py)、[test_candidate_provider.py](/projects/vnpy/tests/test_candidate_provider.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)
  - **文档/进度文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：新增 HK `SIM` / `Futu SIM session` / `live` 顶层包装入口，并把 capability registry 中的 HK `gap.*` 能力替换为正式 capability；`ReadinessGateService` 与 workflow 现会消费本地 `preflight` / `dual_run_diff` / `live report` / `reconciliation` 产物形成 evidence-first 的 simulation/live 门禁；候选池读取规则由"按 market 覆盖"改为"按 `(market, symbol)` merge，dynamic 同 symbol 覆盖 static"，同时 prepare/enrichment 报告新增 `provider_merge_policy`、`requested_runtime_mode`、`runtimes_used`、`single_runtime_effective` 等追溯字段，便于多日验收与复现实验回看

- **2026-05-10**：候选准备产物新增非有限数值清洗，避免输出非法 JSON
  - **代码文件**：[quote_client.py](/projects/vnpy/services/futu_account/quote_client.py)、[candidate_preparation.py](/projects/vnpy/services/strategy/candidate_preparation.py)、[test_candidate_scoring.py](/projects/vnpy/tests/test_candidate_scoring.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)
  - **文档/进度文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)、[beginner_quant_planning.md](/projects/vnpy/.codebuddy/task_list/beginner_quant_planning.md)
  - **影响摘要**：Futu snapshot 中对当前标的不适用的扩展字段若返回 `NaN` / `Infinity`，现在会先在快照归一化阶段转为 `None`，并在 `candidate_prepare` 写回候选 payload / prepare report 前再做一层递归清洗，统一落为 `null`；这样 `state/runs/candidate_inputs.dynamic.json`、`state/runs/candidate_inputs.json` 与 `state/runs/candidate_inputs.prepare.report.json` 都保持严格 JSON，避免下游解析再次被非法数值污染

- **2026-05-10**：候选准备默认 `knot_runtime` 改为 `auto`，并统一对外时间戳为北京时间
  - **代码文件**：[base.py](/projects/vnpy/vnpy_llm/base.py)、[candidate_generation.py](/projects/vnpy/services/strategy/candidate_generation.py)、[candidate_preparation.py](/projects/vnpy/services/strategy/candidate_preparation.py)、[candidate_enrichment.py](/projects/vnpy/services/strategy/candidate_enrichment.py)、[runtime.py](/projects/vnpy/services/knot_runtime/runtime.py)、[remote_runtime.py](/projects/vnpy/services/knot_runtime/remote_runtime.py)、[workflow_service.py](/projects/vnpy/scripts/quant_workflow/workflow_service.py)、[run_quant_workflow.py](/projects/vnpy/scripts/quant_workflow/run_quant_workflow.py)、[run_prepare_candidate_inputs.py](/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py)、[doc_renderer.py](/projects/vnpy/services/evaluation_hub/doc_renderer.py)、[beginner_research.py](/projects/vnpy/services/evaluation_hub/beginner_research.py)、[plan_generator.py](/projects/vnpy/services/evaluation_hub/plan_generator.py)、[beginner_candidate_selector.py](/projects/vnpy/services/evaluation_hub/beginner_candidate_selector.py)、[artifact_store.py](/projects/vnpy/services/evaluation_hub/artifact_store.py)、[unified_schema.py](/projects/vnpy/services/evaluation_hub/unified_schema.py)、[evidence_standardizer.py](/projects/vnpy/services/evaluation_hub/evidence_standardizer.py)、[test_candidate_scoring.py](/projects/vnpy/tests/test_candidate_scoring.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)
  - **文档/进度文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)、[beginner_quant_planning.md](/projects/vnpy/.codebuddy/task_list/beginner_quant_planning.md)
  - **影响摘要**：候选准备与 workflow 的 Knot 默认 runtime 由 `off` 调整为 `auto`，即默认优先尝试 remote、初始化不可用时退回 local；同时 prepare report、workflow summary、artifact、renderer 和 Knot `decision_time` 等对外时间戳统一改为北京时间（`+08:00`），避免再出现与本机时间相差 8 小时的混淆

- **2026-05-10**：候选输入准备链路新增可选真实行情与 Knot enrich 层
  - **代码文件**：[candidate_enrichment.py](/projects/vnpy/services/strategy/candidate_enrichment.py)、[candidate_generation.py](/projects/vnpy/services/strategy/candidate_generation.py)、[candidate_preparation.py](/projects/vnpy/services/strategy/candidate_preparation.py)、[workflow_service.py](/projects/vnpy/scripts/quant_workflow/workflow_service.py)、[run_quant_workflow.py](/projects/vnpy/scripts/quant_workflow/run_quant_workflow.py)、[run_prepare_candidate_inputs.py](/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py)、[test_candidate_scoring.py](/projects/vnpy/tests/test_candidate_scoring.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)
  - **文档/进度文件**：[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)、[beginner_quant_planning.md](/projects/vnpy/.codebuddy/task_list/beginner_quant_planning.md)
  - **影响摘要**：新增独立 `CandidateMarketDataService` 与 `CandidateKnotEnrichmentService`，使 `candidate_prepare` / `HybridCandidateGenerationService.evaluate_single_candidate()` 可以在最终评分前可选接入 Futu snapshot 与 local/remote Knot 结构化评估；准备报告与 workflow step 会额外记录 `include_market_data`、`knot_runtime` 与 enrich 元数据，默认仍保持关闭与安全降级

- **2026-05-10**：引入模块化候选评分接口与 hybrid dynamic/static 生成链路
  - **代码文件**：[candidate_scoring.py](/projects/vnpy/services/strategy/candidate_scoring.py)、[candidate_generation.py](/projects/vnpy/services/strategy/candidate_generation.py)、[candidate_preparation.py](/projects/vnpy/services/strategy/candidate_preparation.py)、[engine.py](/projects/vnpy/services/strategy/engine.py)、[test_candidate_scoring.py](/projects/vnpy/tests/test_candidate_scoring.py)、[test_strategy_engine.py](/projects/vnpy/tests/test_strategy_engine.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)
  - **文档文件**：[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：新增可复用 `CandidateScoringService` 与 `HybridCandidateGenerationService`，把 dynamic/static 候选从"仅清洗旧 JSON"升级为"混合评分生成 + schema 收敛"；`StrategyEngine` 现在会复用同一套 candidate scoring 接口评估单标，准备后的候选输入升级为带 `scoring_model`、`strategy_tags`、`source_breakdown`、`risk_flags` 等字段的 `candidate_inputs_v3`

- **2026-05-10**：新增候选输入前置准备链路与 workflow 可选 prepare 阶段
  - **代码文件**：[candidate_preparation.py](/projects/vnpy/services/strategy/candidate_preparation.py)、[workflow_service.py](/projects/vnpy/scripts/quant_workflow/workflow_service.py)、[run_quant_workflow.py](/projects/vnpy/scripts/quant_workflow/run_quant_workflow.py)、[run_prepare_candidate_inputs.py](/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py)、[test_beginner_quant_workflow.py](/projects/vnpy/tests/test_beginner_quant_workflow.py)
  - **文档/进度文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)、[beginner_quant_planning.md](/projects/vnpy/.codebuddy/task_list/beginner_quant_planning.md)
  - **影响摘要**：新增候选输入准备服务，把 dynamic/static 候选文件统一重写为带 schema 元信息的对象格式，并输出 `state/runs/candidate_inputs.prepare.report.json`；`quant_workflow` 新增可选 `candidate_prepare` 前置阶段与 `--prepare-candidates` CLI 开关，便于在候选观察与 planning 前先收敛本地候选池结构并保留追溯报告

- **2026-05-10**：修复顶层脚本直接执行时的仓库内导入路径
  - **代码文件**：[run_healthcheck.py](/projects/vnpy/scripts/run_healthcheck.py)、[run_portfolio_brief.py](/projects/vnpy/scripts/run_portfolio_brief.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：为顶层 `scripts/` 入口补充 `REPO_ROOT` + `sys.path` bootstrap，使其在仓库根目录直接执行或被 [run.sh](/projects/vnpy/run.sh) 调用时可以稳定导入 `services` 包，避免再次出现 `ModuleNotFoundError: No module named 'services'`

- **2026-05-10**：新增仓库根目录 `run.sh` 安全统一入口
  - **代码文件**：[run.sh](/projects/vnpy/run.sh)
  - **文档/进度文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)、[beginner_quant_planning.md](/projects/vnpy/.codebuddy/task_list/beginner_quant_planning.md)
  - **影响摘要**：新增仓库根目录安全版 `run.sh`，统一封装 `check`、`plan`、`research`、`sim-gate`、`live-gate`、`backtest`、`us-sim` 等入口；默认只做命令预览并打印影响范围，只有显式传入 `--confirm` 才执行；当前刻意不暴露 `us-live` 直通命令，以保持 live 入口的人工确认与硬开关边界

- **2026-05-10**：完成 US live 顶层入口迁移与静态修复收口
  - **代码文件**：[run_us_live_task.py](/projects/vnpy/scripts/run_us_live_task.py)、[__init__.py](/projects/vnpy/services/trading_pipeline/__init__.py)、[run_llm_research.py](/projects/vnpy/scripts/classic_multifactor/run_llm_research.py)
  - **文档/进度文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[task-item.md](/projects/vnpy/.codebuddy/plan/vnpy_wheel_reinvent_audit/task-item.md)、[vnpy_wheel_reinvent_audit.md](/projects/vnpy/.codebuddy/task_list/vnpy_wheel_reinvent_audit.md)
  - **影响摘要**：`scripts/run_us_live_task.py` 不再直接走旧 `LiveTradingPipeline`，而是转发到 `scripts/classic_multifactor/run_intraday_loop.py`；`services/trading_pipeline/__init__.py` 已移除旧 live 导出，减少新的默认调用面；`scripts/classic_multifactor/run_llm_research.py` 的 schema preset 结构已修复，经典多因子脚本的静态编译阻塞已收敛；相关 docs / plan / task_list 已在同轮同步更新

- **2026-05-10**：新增项目级协作治理规则与文档记录约束
  - **规则文件**：新增 [project-collaboration-governance.mdc](/projects/vnpy/.codebuddy/rules/project-collaboration-governance.mdc)
  - **文档文件**：更新 [system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)，新增本记录文档
  - **影响摘要**：后续需及时更新 plan markdown 状态、同步维护历史操作记录、避免在输出中暴露真实账户金额等敏感信息，并在框架/需求/入口改动时同步更新 `docs/` 文档

- **2026-05-10**：优化 `plan` 与 `task_list` 的职责分离约束
  - **规则文件**：更新 [project-collaboration-governance.mdc](/projects/vnpy/.codebuddy/rules/project-collaboration-governance.mdc)
  - **计划/进度文件**：更新 [task-item.md](/projects/vnpy/.codebuddy/plan/beginner_quant_planning/task-item.md)、[beginner_quant_planning.md](/projects/vnpy/.codebuddy/task_list/beginner_quant_planning.md)、[task-item.md](/projects/vnpy/.codebuddy/plan/vnpy_wheel_reinvent_audit/task-item.md)、[task8_cleanup_checklist.md](/projects/vnpy/.codebuddy/plan/vnpy_wheel_reinvent_audit/task8_cleanup_checklist.md)，新增 [vnpy_wheel_reinvent_audit.md](/projects/vnpy/.codebuddy/task_list/vnpy_wheel_reinvent_audit.md)
  - **影响摘要**：明确 `.codebuddy/plan/` 维护计划背景、任务拆解和执行顺序，`.codebuddy/task_list/` 维护权威完成进度；同名计划应一一对应，后续完成状态以 `task_list` 为准

- **2026-05-12**：新增 Knot 四维选股 / 持仓 Review 三个 READ-ONLY 入口
  - **代码文件**：[knot_pick_helpers.py](/projects/vnpy/services/strategy/knot_pick_helpers.py)、[run_knot_4dim_picks_hk.py](/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_hk.py)、[run_knot_4dim_picks_us.py](/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_us.py)、[run_holdings_knot_review.py](/projects/vnpy/scripts/quant_workflow/run_holdings_knot_review.py)、[test_knot_pick_helpers.py](/projects/vnpy/tests/test_knot_pick_helpers.py)
  - **文档文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
  - **影响摘要**：新增 4 维（technical / fundamental / capital_flow / event_driven）Knot 选股入口，HK 与 US 各一个，每维默认 3 个候选并复用本地 `CandidateScoringService.enrich_row("dynamic")` 打分；新增持仓 Review 入口：只读 `FutuAccountProvider.get_summary()` 后，对每只持仓走本地打分 + Knot 给出 `hold|add|trim|exit` 四方向之一；金额、数量、可买力、总资产等敏感字段在 `mask_account_summary` 阶段统一脱敏，落盘 JSON 与 stdout 都不暴露原始数值；持仓占比仅以相对档位 `small (<5%)` / `medium (5~15%)` / `large (>15%)` 表达。Knot 不可用时直接非零退出，不做静默降级。新增的 7 个单测全部通过，并和 `test_candidate_seed_service.py`、`test_candidate_scoring.py` 一同回归（28 个全部通过）。

- **2026-05-13**：tmp 下新增基于 Futu OpenD 的本地行情数据管理（替代 efinance/tushare 数据通路用于 tmp 策略回测）
  - **代码文件**：[__init__.py](/projects/vnpy/tmp/vnpy_futu_data/__init__.py)、[symbol_mapping.py](/projects/vnpy/tmp/vnpy_futu_data/symbol_mapping.py)、[futu_data_manager.py](/projects/vnpy/tmp/vnpy_futu_data/futu_data_manager.py)、[run_futu_data_pull.py](/projects/vnpy/tmp/run_futu_data_pull.py)
  - **数据/文档文件**：[cache_index.json](/projects/vnpy/tmp/data/cache_index.json)（缓存元信息，纳入 git）、[README.md](/projects/vnpy/tmp/vnpy_futu_data/README.md)
  - **影响摘要**：`FutuDataManager` 通过本地 OpenD（默认 127.0.0.1:11111，可经 `FUTU_OPEND_HOST/PORT/PASSWORD` 覆盖）拉取历史 K 线，落入 vnpy 原生 SQLite `~/.vntrader/database.db`；以 `tmp/data/cache_index.json` 维护 `(futu_code, kl_type, autype)` 已覆盖区间，请求时仅拉缺口段，重复请求 0 次 OpenD 调用；DB 唯一索引兜底防重；分页 1000 根 + 间隔 0.5s + 失败指数退避 3 次。支持 HK/US/SH/SZ 现货标的与 1d/1m/5m/15m/30m/60m 周期；5/15/30 分钟级在 vnpy 数据库中折叠到 MINUTE 桶（vnpy 原生 Interval 限制）。仅写本地 SQLite 与本地 JSON，不涉及任何下单链路。已通过实测验证：HK.00700 2024-12 拉取 20 根日线、扩展到 2024-11-01~2025-01-31 仅补 11 月（21 根）+25 年 1 月（19 根）两个缺口、US.AAPL/SH.600519 各市场入库正常、5min 级 96 根入库、再次请求同区间为完全 cache hit（0 次 OpenD 调用）。

## 2026-05-16
- **变更范围**: 策略逻辑调整
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 调整了精简多因子策略的开仓条件为 '(RSI超卖 + 放量) OR (金叉 + 放量)'，并引入了 `used_slices` 全局变量以支持分5份建仓的资金管理逻辑，同时修复了加权平均成本价的计算问题。

## 2026-05-16
- **变更范围**: 策略指标计算修复
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 修复了 `_rsi` 函数，将其改为标准的 Wilder 平滑算法以对齐标准行情软件；修复了 `_volume_ratio` 函数，在计算平均量时排除了当前 K 线，以提高对突发放量的敏感度。

## 2026-05-16
- **变更范围**: 策略补仓逻辑调整
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 增加了补仓策略的限制条件，引入了最小加仓间隔参数（`min_add_interval`，默认10根K线）和最小加仓亏损比例参数（`min_add_loss_pct`，默认2%）。在已有持仓的情况下，只有满足这两个条件才会触发加仓，避免了频繁加仓和在未达到足够跌幅时加仓。

## 2026-05-16
- **变更范围**: 策略卖出逻辑调整
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 修改了卖出策略，引入了移动止盈机制。增加了 `take_profit_pct`（移动止盈激活阈值，默认10%）和 `trailing_drawdown_pct`（移动止盈回撤比例，默认5%）参数，并记录持仓期间的最高价 `highest_price`。当收益率大于激活阈值，且当前价格从最高价回撤超过设定比例时，触发卖出平仓。

## 2026-05-16
- **变更范围**: 策略移动止盈逻辑修正
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 修正了移动止盈的计算逻辑。1. 激活条件由"当前收益率"改为"最高收益率"，防止价格回落导致止盈条件失效；2. 回撤比例的计算基准由"最高价格"改为"最高收益率"，即 `(最高收益率 - 当前收益率) / 最高收益率`。

## 2026-05-20
- **变更范围**: 阶段② 美股多标的量化策略骨架（`us_multi_symbol_quant_phase2`，dry-run only）
- **关键文件**:
  - `phase2/strategy/us_multi_symbol_phase2_strategy.py`（4 因子 + 入场 5 项 + 出场链 + N 日冷却，`LIVE_SUBMIT=False` 硬开关）
  - `phase2/strategy/pool_loader.py` + `phase2/strategy/config/pool_config.yaml`（schema 校验 + 4 类过滤）
  - `phase2/strategy/portfolio_risk.py`（单标 4 条 + 组合 5 条；熔断状态 JSON 落盘可重启恢复）
  - `phase2/runners/run_phase2_backtest.py` / `phase2/runners/run_phase2_reconcile.py` / `phase2/runners/run_futu_perf_baseline.py`（默认 `--dry-run`）
  - `phase2/strategy/tests/`（39 unit tests，全部通过）
  - `docs/research/us_multi_symbol_quant/04_platform_performance_baseline.md` / `05_sim_gate_checklist.md`
  - `docs/system_integration_guide.md` 阶段② 入口章节
- **影响摘要**: 新增阶段② 多标的量化骨架与配套 dry-run / 对账 / 性能基线 runner；不连接 OpenD、不下任何 SIM/REAL 单。从 dry-run 升级到 SIM 必须新开独立 plan 并按 `05_sim_gate_checklist.md` 5 项打勾。

## 2026-05-20 — 阶段② v2：futumd 单文件迁移版策略 + 池周更流水线 + 真实回测 runbook

- **变更范围**: 阶段② v2（plan `us_multi_symbol_quant_phase2_v2`）落地 3 条通道，仍严格 dry-run 不连 OpenD：
  - 新增 futumd 兼容**单文件零依赖**多标策略 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`（严格对齐 NVDA 接口；AST 测试强制无 `from phase2.* / from services.*` import）。
  - 新增池周更 runner `phase2/runners/run_pool_update.py`（默认 `--dry-run`；`--apply` 必须配 `--confirm`；3 道安全闸：变更 > 30% / sector > 40% / 池超限）。
  - 新增候选大池 `phase2/strategy/config/pool_universe.yaml` + 离线行情快照 `pool_metrics_snapshot.json`。
  - 新增双轨真实回测 runbook `docs/research/us_multi_symbol_quant/06_real_backtest_runbook.md`。
  - 新增 11 个单元测试（接口契约 6 + 池更新 5），phase2 测试集 39 → 50 全过。
  - 同步：`docs/system_integration_guide.md` 新增"阶段② v2"章节；`.codebuddy/plan/us_multi_symbol_quant_phase2_v2/`；`.codebuddy/task_list/us_multi_symbol_quant_phase2_v2.md`。
- **影响摘要**: 新增 futumd 平台投放路径、池周度更新通道与真实回测双轨流程；不动现有 `us_multi_symbol_phase2_strategy.py` 与 39 个旧测试。仍然不允许翻 `LIVE_SUBMIT=True`；SIM 升级需要新开 phase3 plan。

## 2026-05-21 — 阶段② live：多标美股天级别 live 交易（phase2/live/）

- **变更范围**: 阶段② live（plan `phase2_live_trading`）落地 `dry_run / futu_sim / futu_real` 三态天级别 live 交易能力，零侵入 phase2 回测；仍硬保 dry_run 默认值，需要 6 开关全部置位 + CLI `--futu-env` + CLI `--live-submit` 才能离开 dry 模式。
- **关键文件**:
  - `phase2/live/order_state.py`（OrderIntent/OrderState 复用 services；新增 `build_request_id` / `transition` / `OrderStateStoreExt`）
  - `phase2/live/risk.py`（从 `scripts/classic_multifactor/risk.py` 物理 copy 后改 phase2 命名空间，去掉 classic_multifactor 依赖；幂等性交给 IdempotencyGate）
  - `phase2/live/safety.py`（6 开关：VNPY_LIVE_CONFIG / VNPY_LIVE_SUBMIT / VNPY_LIVE_APPROVED / FUTU_TRADE_PASSWORD / `--futu-env` / `--live-submit`）
  - `phase2/live/broker.py` + `phase2/live/futu_broker.py`（LiveBroker 协议 + `OpenSecTradeContext` + `OpenQuoteContext` 实现）
  - `phase2/live/live_adapter.py`（与 `phase2/backtest/futumd_strategy_adapter.py` 同接口的 live runtime）
  - `phase2/live/guards.py`（4 级 pre-trade gate：幂等 → 对账 → 单标风控 → 组合风控；events.jsonl + 状态机推进）
  - `phase2/live/runner.py`（`DailyLiveRebalanceRunner`，clock/sleep 全注入；REAL 强制 auto_cancel_on_eod；RunnerConfig 新增 `strategy_live_submit_override` 选项供 dry_run smoke 走通全链路）
  - `phase2/runners/run_phase2_live_daily.py`（CLI 入口，所有重 import lazy 化；产物三态分目录；与 phase2 其他 runner 对齐的 `_REPO_ROOT` 自注入 `sys.path`）
  - `phase2/live/tests/`（132 项新增单测，全部通过；含 InMemoryBroker + FakeClock + 临时 strategy 文件，不依赖外部网络）
  - `docs/system_integration_guide.md`（新增"阶段② live"章节）
  - `.codebuddy/plan/phase2_live_trading/`（背景与需求；进度以同名 `task_list` 为权威）
  - `.codebuddy/task_list/phase2_live_trading.md`（10 项任务进度表）
- **影响摘要**: 在 phase2 回测之上补齐了真实 OpenD 连接 + SIM/REAL 天级别 live 交易闭环；4 级 gate + 6 开关 + 状态机 + 三态分目录产物全部可单测。phase2 现有 75 个回测单测全数复跑通过；新增 132 项 live 单测（phase2/ 全集 215/215，1.06s）；dry_run smoke 已跑通产物落盘于 `state/runs/phase2_live/dry_run/smoke_dry_run_t10/`（`daily_report.json` + `positions_snapshot.csv` + 空 `orders/`）；既有 `phase2/strategy/*` 与 `phase2/backtest/*` 源码零改动；`tmp/` 与 `scripts/classic_multifactor/` 零改动。仍**不允许**直接连真实账户下单——必须 6 开关全置 + 人工审批后再动手。

## 2026-05-23 phase2 策略自我优化闭环（双 subagent，纯本地回测）

- **变更范围**: 在 phase2 之上新增 `phase2.optimize` 子包与 `phase2/runners/run_phase2_strategy_self_optimize.py` CLI；引擎补一个安全的 `param_overrides` 注入钩子；新增搜索空间 YAML 与 6 个 pytest 文件；同步 `docs/system_integration_guide.md`、`.codebuddy/task_list/phase2_strategy_self_optimize.md`。
- **关键文件**:
  - `phase2/optimize/__init__.py`（包级 `assert_no_live_imports` 守卫）
  - `phase2/optimize/session.py`（`SessionPaths`、session_id 校验、与 `phase2_multi_backtest` 的命名空间冲突保护）
  - `phase2/optimize/io_schemas.py`（`Proposal` / `TrialResult` / `Evaluation` / `IterEvaluations` / `LeaderboardRow` / `SessionSummary` + JSON/CSV 原子写）
  - `phase2/optimize/search_space.py`（YAML 加载、frozen 拒绝、`HARD_CEILINGS` 防御）
  - `phase2/optimize/trial_runner.py`（构建 `PortfolioBacktestEngine(force_live_submit=True, param_overrides=...)`，捕获异常 → `injection_mismatch` / `failed`）
  - `phase2/optimize/optimizer.py`（grid / random / local-perturb / explore + LLM 钩子失败降级）
  - `phase2/optimize/evaluator.py`（多维归一化打分 + ranking + continue/stop 决策 + 5 项诊断）
  - `phase2/optimize/coordinator.py`（主循环；progress.log；resume 支持；磁盘下限 1 GB）
  - `phase2/optimize/llm_bridge.py`（`OpenAICompatibleClient` 包装；`PHASE2_OPT_LLM_*` env vars；任何失败返回空降级）
  - `phase2/optimize/reporter.py`（`REPORT.md` + 与 `fixed_pool_5y_a1` 基线对比表）
  - `phase2/runners/run_phase2_strategy_self_optimize.py`（CLI，含 `--dry-run` / `--resume` / `--print-leaderboard` / `--llm-*`）
  - `phase2/backtest/portfolio_backtest_engine.py`（新增 `param_overrides: Optional[Dict[str, Any]]`，在 `initialize()` 之后做 `setattr` + `getattr==value` 一致性断言）
  - `phase2/strategy/config/optimize_search_space.yaml`（18 个安全可调参数 + frozen + 硬上限）
  - `phase2/strategy/tests/test_optimize_search_space.py`（10 项）
  - `phase2/strategy/tests/test_optimizer_subagent.py`（8 项）
  - `phase2/strategy/tests/test_evaluator_subagent.py`（8 项）
  - `phase2/strategy/tests/test_optimize_trial_runner.py`（5 项）
  - `phase2/strategy/tests/test_optimize_coordinator_dryrun.py`（3 项）
  - `phase2/strategy/tests/test_no_live_imports.py`（9 项子进程隔离断言）
  - `state/runs/phase2_strategy_self_optimize/.gitignore`（产物目录占位 + 排除）
  - `docs/system_integration_guide.md`（新增"阶段③ phase2 策略自我优化闭环"章节）
  - `.codebuddy/plan/phase2_strategy_self_optimize/`（背景与需求；进度以同名 `task_list` 为权威）
  - `.codebuddy/task_list/phase2_strategy_self_optimize.md`（10 项任务进度表）
- **影响摘要**: 在 phase2 多标回测之上叠加了一套"提案 → 注入 → 回测 → 评估 → 决策"的双 subagent 自优化闭环；策略源码零改动（参数全部走 `param_overrides` setattr 注入）；frozen_param 集合 + 三参数硬上限 + 9 项子进程级 import 守卫共同把闭环锁在 `force_live_submit=True` 的本地回测分支，禁止任何 OpenD/Futu 接触；新增 44 项 pytest 全绿，phase2/strategy 全集 119/119 通过；dry-run smoke 在 0.1 秒内完整跑通 2 iter × 2 trial，产物结构与 REPORT.md 渲染均符合预期。仍**不允许**自动落地"最优参数"——必须由人手动整理为新 baseline、再走原 phase2 多标回测复跑确认后，才可考虑替换运行参数。

## 2026-05-23 phase2 策略自优化首次真实 5 年区间闭环（session `opt_20260523T150600Z`）
- **变更范围**: 运行产物（不修改代码）。首次以真实 5 年日线（2021-05-23 → 2026-05-22）固定池（NVDA、MSFT、AVGO、TSM、TSLA、AMZN）启动 phase2 自优化双 subagent 闭环，验证全链路在生产时长下的稳定性。
- **运行配置**: `--max-iters 10 --trials-per-iter 4 --top-k 2 --rate 0.0003 --slippage 0.0 --init-cash 100000 --max-runtime-min 90 --patience 3 --min-delta 0.5`；`--llm-optimizer/--llm-evaluator` 已开启但未配置 `PHASE2_OPT_LLM_*` env，按设计自动降级为本地规则路径，全程未发起远端 LLM 请求。
- **结果**: 10 iter × 4 trial = 40 次回测全部 ok，总耗时约 30 秒；最佳 trial = `iter02_trial03`（origin=`explore`），score=0.8，年化 ~6.67%、最大回撤 ~10.92%、夏普 ~0.76、卡玛 ~0.61、交易 309 次；vs `fixed_pool_5y_a1` 基线 +约 11 个百分点总收益、回撤改善 ~1.9 个百分点。停机原因 `max_iters`，近 3 iter 未持续刷新最佳分。
- **关键参数差异（best vs default）**: `max_slices 5→2`、`pool_budget_pct 0.8→0.6`、`stop_loss_pct 0.05→0.04`、`take_profit_pct 0.10→0.29`，其余 14 个参数维持默认。
- **关键产物**:
  - `state/runs/phase2_strategy_self_optimize/opt_20260523T150600Z/REPORT.md`（含每 iter leaderboard 与最佳参数 diff、与 baseline 对比表）
  - `.../iter_02/trial_03/{summary.json,applied_params.json,...}`（best trial 详情）
  - `.../session_summary.json` / `.../progress.log`
  - 整个 session 目录受 `state/runs/phase2_strategy_self_optimize/.gitignore` 排除，不进入 git。
- **影响摘要**: 自优化闭环在真实 5 年区间下端到端跑通，纯本地降级路径表现稳定（与 dry-run 结果同形）；最佳参数仍属"建议项"，按规则**不**自动落地——需后续人工以新 baseline 走 `phase2/runners/run_phase2_multi_backtest_real.py` 复跑确认后再决定是否替换默认参数。

## 2026-05-23 phase2 策略 v2 趋势跟随重构（plan `phase2_strategy_redesign_v2`）
- **变更范围**: 新增 v2 单文件策略 + 计划/任务 + 系统集成指南 v2 章节 + REPORT；
  v1 文件保持不动以做 A/B 对照。
- **新增/修改文件**:
  - `phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2.py`（489 行单文件，
    stdlib only，零本地 import，私有 helper，LIVE_SUBMIT 默认 False）
  - `.codebuddy/plan/phase2_strategy_redesign_v2/{requirements,task-item}.md`
  - `.codebuddy/task_list/phase2_strategy_redesign_v2.md`
  - `docs/system_integration_guide.md`（在 futumd 策略章节内追加 "v2 趋势跟随版" 子章节）
  - `state/runs/phase2_strategy_redesign_v2/20260523T153754Z/{REPORT.md,v1_latest/,v2_default/}`
- **设计要点**: 入场从 5 条 AND（MA + RSI + vol_ratio + ATR-cap + concurrent）
  改为 3 条 AND（SMA200 regime + SMA100 长期 + Donchian55 突破）；出场从
  固定 TP/trailing-dd/fast-slow 死叉/ATR-cap 改为 Chandelier 止损（max_since_entry
  - 3·ATR(22)）+ 趋势离场（Close < SMA(50)）；预算公式去掉 position_pct 二次缩放，
  per_symbol_budget = NAV × 0.95 / 6；新增 vol-targeting (target_vol=0.15, scale ∈
  [0.4, 1.5])；单仓位无加仓。
- **5 年回测对照（同区间同池同手续费）**:
  - v1 latest：total_return +26.85%、年化 +4.89%、MaxDD 12.83%、315 trades、胜率 42%
  - v2 default：total_return **+32.49%**、年化 +5.81%、MaxDD **6.18%（减半）**、
    135 trades、胜率 **52%**
  - v1 复跑数值与历史 `fixed_pool_5y_a1` 完全一致 → 回测可重复。
- **影响摘要**: v2 在 mega-cap 趋势池上验证了"少做、做对、跟住"的趋势跟随范式
  在本仓库引擎 + 兼容硬约束下可正确落地；v1 保留作为参照，runner / adapter / 引擎
  / pool_loader 均零改动；现有 phase2 测试套件 119/119 仍全绿。**LIVE_SUBMIT 仍硬
  开关 False；本轮没有任何真实订单/远端连接发生**；仅本地数据库回测产物落盘。

## 2026-05-23（晚）phase2 v3 趋势跟随策略迭代优化完成
- **变更范围**: 在 v2 基础上做 6 轮迭代优化（iter1~iter6），最终选定 iter5 提升为 v3，达成 5 年回测 +320% 目标。
- **新增文件**:
  - `phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py`（最终版，单文件 stdlib only，零本地 import，完整继承所有硬约束）
  - `phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2_iter1.py` ~ `v2_iter6.py`（中间溯源版本，保留以做归因）
  - `state/runs/phase2_strategy_redesign_v2/20260523T153754Z/REPORT_OPTIMIZATION.md`（全程对照报告）
  - `state/runs/.../v2_iter1/`~`v2_iter6/`、`v3_final/` 7 套回测产物
- **设计要点**:
  - 入场更早：Donchian 55→10 抓拐头；regime 仅保留 SMA(200) 单层闸门
  - 出场更宽：Chandelier 3→6×ATR、ATR 22→40、ma_exit SMA50→SMA150
  - 单仓更重：max_concurrent 6→3，单仓 ~33% NAV
  - 抗洗：`min_hold_bars=10` 入场宽限期，`disaster_loss_pct=0.12` 单仓硬止损
  - 回撤护栏：`regime_flat`（SMA200 下穿即清仓）
  - 滚雪球：金字塔加仓 max_slices=3，每涨 1×ATR 加 0.5×base
- **5 年回测对照**（pool_config_fixed.yaml 6 只 / 100k / fee=0.0003）:
  - v1 latest: +26.85% / MDD 12.83%
  - v2 default: +32.49% / MDD 6.18%
  - **v3 final: +320.49% / 年化 33.40% / MDD 23.92% / 82 trades** ✅ 超 200% 目标 120 pp
- **失败试验记录**: iter6 引入 portfolio-level 15% 高水线 dd_cut 导致初期触发后无法恢复，最终只 +12.76% — 全局 dd_cut 在高 beta mega-cap 池上不可用，应坚持单仓位级硬止损 + regime 翻负清仓。
- **影响摘要**: phase2 测试套件 119/119 仍全绿；v1 / v2 文件未改动以保留 A/B 基线；LIVE_SUBMIT 仍硬开关 False；本轮 7 次回测均为本地数据库读取，无任何远端连接、无任何真实订单。
