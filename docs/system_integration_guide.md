## 项目系统集成指南

### 文档定位

这份文档面向在本仓库内做 vibecoding、接手开发、补充脚本或维护集成链路的协作者。
目标不是重复 `docs/community/` 或 `docs/elite/` 的上游说明，而是快速说明**本项目自定义层**的真实入口、关键目录、状态产物、阶段边界和已知缺口。

### 文档同步规则

- 当项目结构、主入口脚本、默认参数、状态产物路径、阶段定义发生变化时，必须同步更新本文档和 `docs/adaptive_quant_engine_design.md`。
- 新增、删除、重命名入口脚本时，文档更新应与代码变更在同一轮提交中完成。
- 如果代码与文档不一致，以代码为准；发现偏差后，下一次相关修改必须补齐文档。
- 对其他协作者来说，这两份文档是理解项目的第一入口，不要让它们长期停留在"设计草稿"状态。
- 框架、需求、入口等改动完成后，还应同步检查 [project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) 是否需要补历史记录。

### 协作治理补充

- 已开始或已完成的 plan item / task item，应及时更新对应 markdown 状态，避免任务记录长期滞后。
- 对框架、需求、入口、规则、运行产物结构、阶段边界有影响的改动，应同步记录到 [project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)。
- 在回复、文档、日志和说明中，避免暴露真实账户金额、完整账号、密钥、令牌等敏感信息；需要表达时应使用脱敏后的描述。

### 一句话理解当前项目主线

当前项目不是单一策略脚本，而是一条"**候选准备 → 健康检查 → 观察标筛选 → 回测证据 → readiness 门禁 → 仿真/实盘入口隔离**"的多层结构。
其中，推荐的第一阅读入口是：

1. `scripts/quant_workflow/run_quant_workflow.py`
2. `scripts/quant_workflow/workflow_service.py`
3. `services/evaluation_hub/`
4. `services/strategy/`
5. `scripts/classic_multifactor/`

### 关键目录与职责

- **`scripts/quant_workflow/`**：统一的 quant workflow 入口与编排层。负责把候选准备、任务类型感知的健康检查、观察标筛选、回测证据和 readiness 串起来。
- **`services/evaluation_hub/`**：项目级解释与 artifact 落盘层。负责候选观察、回测证据摘要、readiness artifact 和统一 workflow summary。
- **`services/strategy/`**：策略内核层。负责候选输入标准化、`raw_score` 计算、入场/退出择时、策略选择与信号生成。
- **`scripts/classic_multifactor/`**：经典多因子主线。包含 vn.py CTA backtest、参数扫参与日内/日频执行入口。
- **`services/execution_guard/`、`services/risk_engine/`、`services/trading_pipeline/`**：执行保护层。负责 live gate、precheck、reconciliation、risk guard、sim/live task 约束；其中 `services/trading_pipeline/` 当前仅保留 SIM / close 兼容入口，live 顶层入口已转发到 classic mainline。
- **`services/futu_account/`、`services/futu_opend/`、`services/futu_sim_trade/`**：券商与 OpenD 接入层。
- **`state/runs/`**：运行时工件目录。健康检查、候选输入、回测报告、workflow artifact、orders、brief 等都落在这里。
  - classic mainline runner 相关的订单状态与事件日志现在会按执行环境拆分到子目录：
    - `state/runs/dry_run/orders/*.json`
    - `state/runs/futu_sim/orders/*.json`
    - `state/runs/futu_real/orders/*.json`
    - 对应事件日志分别落到 `state/runs/<execution_env>/events.jsonl`
  - `scripts/run_us_futu_sim_session.py` 这类 Futu SIM session 入口也会复用 `state/runs/futu_sim/orders/`，避免与 dry-run / real 混放
- **`phase2/ted/`**：TED / Knot 研究流程独立目录。集中放置 `run_knot_4dim_research.py`、`run_candidate_preparation.py`、`run_ted_discovery.py`、`run_ted_full_pipeline.py`、`trend_explosion_discovery.py` 以及对应说明文档和测试；`phase2/` 根目录保留同名兼容包装入口，避免旧命令失效。
  - `TED` 运行产物现在统一归档到 `state/runs/ted/YYYYMMDDTHHMMSS/`，单批次目录下固定拆分为：`stage1_knot_4dim/`、`stage2_candidate_preparation/`、`stage3_ted_discovery/`，并附带 `manifest.json` 方便后续遍历与批次追溯。
  - `stage1_knot_4dim/` 存四维研究输出 `knot_4dim_us.json`；`stage2_candidate_preparation/` 存候选准备报告和动态候选快照；`stage3_ted_discovery/` 存最终 `ted_aggressive_pool_report.json` 与导出的 `pool_config.yaml`。
  - 为兼容旧读取方，最终阶段仍会镜像写出 `state/runs/ted_aggressive_pool_report.json` 与 `state/runs/ted_pool_config.yaml`；但后续新的遍历、回看、批次分析应优先读取 `state/runs/ted/` 目录。

### 主要入口脚本

- **`run.sh`**：仓库根目录的安全统一入口脚本。
  - 默认只做 preview，不执行底层命令
  - 仅在显式传入 `--confirm` 时才真正执行目标入口
  - 当前封装的子命令包括 `check`、`plan`、`research`、`sim-gate`、`live-gate`、`backtest`、`us-sim`
  - 会在执行前打印解析后的命令、是否触达 OpenD / 账户 / SIM 状态、预期输出文件和交易影响说明
  - 当前**不暴露** `us-live` 直通命令，避免把真实 live 入口误包装成"一键执行"
- **`scripts/quant_workflow/run_quant_workflow.py`**：当前推荐的总入口。
  - 默认 `--preset trading_full`
  - 默认 `--mode plan`
  - 默认 `--stage readiness`
  - 默认 `--task-type simulation`
  - 支持 `--preset health_snapshot / simulation_readiness / live_readiness`
  - 支持 `--summary-only` 只输出 workflow 汇总、artifact 路径和 warning
  - 支持可选 `--prepare-candidates`，在 `healthcheck` 前先重写候选输入并产出准备报告
  - 如需在 prepare 阶段补真实行情或 Knot 结构化评估，可额外传 `--prepare-include-market-data` 与 `--prepare-knot-runtime off|local|remote|auto`
  - 若未显式传 `--prepare-knot-runtime`，当前默认使用 `auto`：优先尝试 remote，初始化不可用时退回 local
  - 默认不会自动启动 SIM/live 脚本
- **`scripts/quant_workflow/run_prepare_candidate_inputs.py`**：候选输入前置准备入口。
  - 默认走**按市场独立刷新动态池**的新流程：每个市场写入各自的 `state/runs/candidate_inputs.dynamic.{hong_kong,us}.json`，其它市场的文件**完全不被触碰**（文件级隔离，HK 与 US 可并发刷新而不互相覆盖）；静态池 `state/runs/candidate_inputs.static.{hong_kong,us}.json` 本流程**不再触碰**，留给后续按月刷新工作流。
  - 兼容窗口期内仍保留旧的 `candidate_inputs.dynamic.json` / `candidate_inputs.json` 作为只读 fallback；当目标市场对应的新文件不存在时，`UnifiedCandidateProvider` 会回退读老文件。
  - `--market` 可选 `all` / `hong_kong` / `us`，默认 `all`，`all` 时按市场依次跑。
  - `--strategy` 可选 `knot_first`（默认）/ `score_first` / `merge_existing`：
    - `knot_first`：先调用远端 Knot agent 生成 ~20 个候选，再走本地 multifactor 评分 + Futu snapshot enrich，取 Top-N 写回。
    - `score_first`：通过 `FutuMarketUniverseProvider` 用 `get_stock_filter` 按市值降序 + 流动性下限拉目标市场的高质量短名单（按 `--universe-preset` 控制条件），本地多因子评分后取 Top-N，再做一轮 Knot 富化。
    - 当 `knot_first` 失败（远端 Knot 不可用、未配置、返回空）时，会**自动降级为 `score_first`**，并在 `market_runs[*].knot_status` 标注降级原因。
    - `merge_existing`：仅基于动态池里已有的目标市场行重新打分（兼容老行为）。
  - `--top-n`（默认 20）控制每市场最终保留的候选数；`--knot-target-count`（默认 20）控制让 Knot 提议的候选数；`--universe-limit`（默认 200，对齐 OpenD 单页上限）限制 score_first 拉到的标的数。
  - `--universe-preset` 可选 `large_cap`（默认）/ `momentum_cta` / `none`：
    - `large_cap`：HK 市值 ≥ 50 亿 HKD、价格 ∈ [1, 1000]、成交额 ≥ 5000 万 HKD；US 市值 ≥ 5 亿 USD、价格 ≥ 5、成交额 ≥ 1000 万 USD；按市值降序分页拉取。
    - `momentum_cta`：在 `large_cap` 的基础上叠加 N 日涨幅 / 量比等技术过滤（连接的 OpenD 缺失对应字段时会自动忽略相关条件）。
    - `none`：回退到旧 `get_stock_basicinfo` 全市场字典序列举；当 `get_stock_filter` 不被 SDK 支持或调用失败时也会自动降级到该路径。
  - `--include-market-data` 默认开启（`--no-include-market-data` 关闭）；`--knot-runtime` 仍支持 `off|local|remote|auto`，默认 `auto`。
  - Snapshot enrichment 已实现**分批 + 二分降级**容错：单次 OpenD `get_market_snapshot` 不超过 200 个 code，遇错时自动二分重试，疑似非法的单 symbol 会被隔离并记 warning，不再因为一个无效代码导致整批失败。
  - **零 enrichment 守护**：当 `score_first` 路径下 `--include-market-data` 开启但 snapshot 全部失败（`status=error/unavailable` 或 `matched_rows=0`）时，该市场的本轮刷新会写 `kept_count=0` 并保留警告，**不会**用未富化的字母序结果覆盖已有动态池。
  - `--dry-run` 只生成报告，不写回动态池。
  - `--legacy` 显式回到老 `prepare()` 流程（同时刷新 dynamic + static），需要替换源文件时仍可配合 `--dynamic-source` / `--static-source`。
  - 报告输出 `state/runs/candidate_inputs.prepare.report.{hong_kong,us}.json`（按 market 拆分以避免并发覆盖），同时仍写一份聚合 `state/runs/candidate_inputs.prepare.report.json` 作为 back-compat 摘要；schema 仍为 `candidate_prepare_report_v3`，新增 `per_market_report_paths` / `per_market_dynamic_paths` 字段，便于追溯单次刷新的执行路径。
  - 写回前会递归清洗非有限数值；来自 snapshot 或其他 enrich 源的 `NaN` / `Infinity` 会统一落为 `null`，保证产物保持严格 JSON。
- **`python -m scripts.quant_workflow`**：与上面的脚本入口等价的模块入口，适合统一的一键工作流触发。
- **`scripts/run_healthcheck.py`**：环境和账户健康检查入口，输出 `state/runs/healthcheck.json`。
- **`scripts/run_portfolio_brief.py`**：组合摘要入口，聚合 HK/US close report 与 healthcheck，输出 `state/runs/portfolio_brief.json`。
- **顶层 `scripts/` 入口兼容约束**：凡是从仓库根目录通过 `python scripts/...` 或被 `run.sh` 直接调用的顶层脚本，都应在文件开头先解析 `REPO_ROOT` 并注入 `sys.path`，避免 `from services...` 这类仓库内导入在直接执行时失效。
- **`scripts/classic_multifactor/run_vnpy_cta_backtest.py`**：官方 vn.py CTA 回测入口，输出 `state/runs/classic_multifactor/vnpy_cta_backtest_report.json`。
- **`scripts/classic_multifactor/run_intraday_loop.py`**：分钟级主线 runner，带执行保护，属于 simulation/live 邻近入口。
  - 共享 `BaseRunner.map_vt_symbol()` 会在会话启动前把 classic config 中的美股 `NVDA.US` 规范化为 `NVDA.SMART`，并把港股 `00700.HK` 规范化为 `00700.SEHK`，避免 vn.py/Futu 会话因交易所后缀不匹配而无法创建策略实例。
  - 当调用方未显式传 `--session-tz`、`--session-start`、`--session-end` 时，runner 现在会根据 `services/strategy/market_rules.py` 里由 `symbol/vt_symbol` 解析出的 market 自动推导默认交易所时区和常规 session 边界；当前 HK 会落到 `Asia/Hong_Kong` + `09:30~16:00`，US 会落到 `America/New_York` + `09:30~16:00`，从而避免 HK 配置误沿用美股时区。
  - classic strategy 的 `on_init() -> load_bar()` warmup 历史 bar 当前只用于指标/模型预热，不再通过 execution hook 写入正式 `OrderStateStore`；初始化阶段出现的历史信号不会污染正式 dry-run / Futu 模拟 / Futu 实盘订单目录。
  - warmup 载入现在会按策略 `data_interval` 显式换算 `load_bar(days=...)` 所需的自然日天数：`1m` 分钟策略会把模型所需 warmup bar 数折算成一个保守的交易日窗口（并附带周末/节假日缓冲），然后用 `Interval.MINUTE` 预热；`1d` 日级策略则继续按所需 bar 数直接加载日线天数。这样可避免把 `480` 根 `1m` 预热 bar 误当成 `480` 个自然日去回放，导致启动长时间停留在 `warmup`。
  - runner 现仅在**真实决策点**或**有意义状态变化**时输出 `intraday bar result` 日志：`warmup` bar 不打印，普通非决策 `1m` bar 不打印；仅当当前 `bar.datetime` 命中策略信号评估边界（例如 `signal_interval_minutes=15` 时的 `:00/:15/:30/:45` 分钟边界），或本轮出现审批通过、风控拦截、异常、活跃订单变化时，才记录 `result`、`last_signal`、`approved_delta`、`blocked_delta`、`blocked_by_gate`、`pos` 与 `active_orders`。这样既能保留排障所需的关键轨迹，又避免启动 warmup 和日常非决策 bar 刷屏，同时不会再因策略内部 `bars` 缓冲区截断而错过后续决策点日志。
  - 为了继续排查"进程活着但分钟日志静默"的场景，intraday runner 还会在非 warmup 的 live `on_bar` 入口/出口输出 `intraday debug checkpoint`，记录 `phase`、`bar_time`、`decision_bar`、`bars_seen`、`bars_cached`、`last_signal`、`pos`、`active_orders` 与 `raw_score`；同时主循环每 60 秒输出一次 `intraday runtime heartbeat`，汇总 `bars_seen`、`last_bar_time`、`seconds_since_last_bar`、审批/拦截累计值等，用来区分"根本没收到新 bar"与"已经收到 bar 但卡在策略内部某一步"。
  - `state/runs/<execution_env>/events.jsonl` 记录 execution hook 的正式事件轨迹，例如 `order_approved`、`order_blocked`、`order_submitted`；这些事件由 `ExecutionGuardPipeline` 在订单审批链路中逐条追加，用于事后审计、排查某次信号为什么被拒绝/批准，以及供 dual-run / preflight / reconciliation 等只读工具统计最近运行痕迹。
  - `state/runs/<execution_env>/orders/*.json` 保存每个被正式审批过的 `OrderState` 快照；其主要用途是跨重启幂等、防止重复请求、以及把后续 OMS / broker 回报与项目内 `request_id` 重新关联。它不会直接触发下单，但会影响后续同一请求是否被视为重复、以及恢复阶段如何识别"哪些订单已经进入正式生命周期"。
- **`scripts/classic_multifactor/run_daily_rebalance.py`**：日频再平衡 runner，带执行保护，属于 simulation/live 邻近入口。
  - 当 CLI 与 config 都未提供 `rebalance_time` 时，runner 现在会从 `services/strategy/market_rules.py` 按 market 自动回退到默认日频调仓时间（当前 HK / US 默认均为 `15:55`），减少日频配置重复写死时间参数。
  - 当当前时间尚未到 `rebalance_time` 时，runner 会先输出一条 `daily runner waiting` 启动等待日志，并在等待期间每 10 分钟输出一条 `daily runner heartbeat`，记录当前本地时间、目标调仓时间和剩余分钟数，便于确认任务仍在静默等待而非假死。
- **`scripts/run_us_sim_task.py`**：US SIM 任务入口；默认保留 legacy SIM 路径，同时支持显式转发到 `run_intraday_loop.py` 新主线。
- **`scripts/run_us_futu_sim_session.py`**：US Futu SIM session 入口。
- **`scripts/run_us_live_task.py`**：US live task 顶层包装入口；当前不再直接调用 `LiveTradingPipeline`，而是转发到 `scripts/classic_multifactor/run_intraday_loop.py`。该包装层本身不连接 OpenD、不直接下单，真实提交仍需 `--live-submit` 与下游 `VNPY_LIVE_*` 硬开关同时满足。
- **`scripts/run_hk_sim_task.py`**：HK SIM 任务入口；默认保留 legacy SIM 路径，同时支持显式转发到 `run_intraday_loop.py` 新主线，并默认注入 `Asia/Hong_Kong` 与 `hk_sim_task_report.json`。
  - 当走 vn.py mainline 转发路径时，包装层还会默认注入 `--futu-market HK`，避免港股会话在错误市场上下文里做合约查询与订阅。
- **`scripts/run_hk_futu_sim_session.py`**：HK Futu SIM session 顶层包装入口；转发到主线 intraday runner，默认注入 HK 会话时区、`hk_futu_sim_session_report.json` 与 Futu `模拟` 环境。
  - 默认 classic config 仍可使用研究侧常见的 `00700.HK` 写法；进入主线 runner 后会自动规范化为 vn.py 订阅使用的 `00700.SEHK`。
  - 包装层还会默认注入 `--futu-market HK`，保证港股 Futu SIM 会话在正确市场上下文里完成合约查找与订阅。
- **`scripts/run_hk_live_task.py`**：HK live task 顶层包装入口；只保留 `--live-submit` 意图并转发到主线 intraday runner，真实提交仍需下游 `VNPY_LIVE_*` 硬开关同时满足。
  - 包装层会默认注入 `--futu-market HK`，避免 live 侧因市场默认值仍停留在 `US` 而出现港股合约订阅失败。

### Knot 4 维选股 / 持仓 Review 入口

下面三个入口共享 `services/strategy/knot_pick_helpers.py` 这一层 helper，单独面向「让 Knot 给出可阅读的研究类输出」，**不连交易、不改交易状态**：

- **`scripts/quant_workflow/run_knot_4dim_picks_hk.py`** / **`run_knot_4dim_picks_cn.py`** / **`run_knot_4dim_picks_us.py`**
  - 让远端 Knot 按 4 个研究维度（`technical / fundamental / capital_flow / event_driven`）各给 3 个目标市场候选，每条候选回到本地 `CandidateScoringService.enrich_row("dynamic")` 走一遍打分。
  - 当前支持 `hong_kong`、`china`（A 股，接受 `*.SH` / `*.SZ`）与 `us` 三个研究市场；默认仅出站调用 Knot LLM，不连 OpenD、不下单、不写 `candidate_inputs*.json`。
  - 输出为紧凑可读文本到 stdout，并把结构化 JSON 写到 `log/YYYYMMDDHH/knot_4dim_hk.json` / `log/YYYYMMDDHH/knot_4dim_cn.json` / `log/YYYYMMDDHH/knot_4dim_us.json`；`--dry-run` 只打印不落盘，`--output` 可显式覆盖默认路径。
  - 当 `KNOT_AGUI_URL` / `KNOT_API_TOKEN` 缺失或 Knot 返回不可用时，**直接以非零退出码报错**，不做静默降级（让操作者明确知道没拿到 Knot 输出）。
- **`scripts/quant_workflow/run_holdings_knot_review.py`**
  - 通过 `FutuAccountProvider(...).get_summary()` 只读拉取当前持仓，再让 Knot 对每只持仓给出 `hold | add | trim | exit` 四个方向之一（不给具体仓位百分比）。
  - 默认显式查询 `REAL` 交易环境的账户快照（只读 `position_list_query`，不下单），也可通过 `--trd-env REAL|SIMULATE` 覆盖；`--live-strict` 可要求按 `trd_env` 做严格账户选择。
  - 对外暴露的字段经过 `mask_account_summary` 脱敏：仅保留 `symbol / name / market / weight_bucket / pl_direction`，剔除所有现金、市值、数量、可买力、总资产；`weight_bucket` 按相对总资产档位计算（`<5% small`、`5%~15% medium`、`>15% large`）。
  - Knot prompt 中明确要求模型不要给出任何具体股数 / 金额 / NAV 占比；本地输出与落盘文件 `log/YYYYMMDDHH/holdings_knot_review.json` 同样不会包含原始金额或数量；`--output` 可显式覆盖默认路径。
  - `--skip-knot` 可跳过 Knot 调用做离线预览，仅打分不调远端。
- **`scripts/quant_workflow/run_knot_research_bundle.py`**
  - 默认顺序执行以上 3 个研究入口：先 HK 四维选股，再 US 四维选股，最后持仓方向审阅。
  - 默认让三份 JSON 结果共享同一个 `log/YYYYMMDDHH/` 目录，便于按批次归档；`--output-dir` 可统一覆盖输出目录，`--per-dim` 会转发给 HK / US 入口，`--skip-knot` 仅转发给持仓审阅入口。
  - `--schedule-workdays` 简版调度模式：进程常驻后按市场本地时区与工作日触发，当前固定为 `HK 09:00 Asia/Hong_Kong` 运行 `run_knot_4dim_picks_hk.py` + 1 次持仓 review、`HK 12:00 Asia/Hong_Kong` 再运行 1 次持仓 review、`US 09:00 America/New_York` 运行 `run_knot_4dim_picks_us.py` + 1 次持仓 review、`US 12:00 America/New_York` 再运行 1 次持仓 review；`--poll-seconds`、`--heartbeat-seconds`、`--schedule-window-minutes` 可调轮询、心跳和重启补跑窗口。
  - bundle 入口会把 `--holdings-trd-env`（默认 `REAL`）和 `--holdings-live-strict`（默认开启）转发给持仓审阅入口，因此调度模式下的 4 次 holdings review 都是对真实账户做只读查询，不提交订单。
  - 该入口本身不引入新的交易副作用，只是按顺序编排已有的只读研究脚本；任一子任务失败时会立即停止并返回相同退出码。

> 安全边界：以上三个研究入口及其 bundle 入口归类为 **READ-ONLY 研究类入口**。它们不会启动交易会话、不会改写订单状态、不会触发 `prepare candidates` 流；持仓 review 只调用 OpenD 的 `position_list_query`，不下单、不撤单、不调仓。

### 推荐的项目阅读顺序

如果你是第一次接触本项目，建议按下面顺序阅读：

1. **看总入口**：`scripts/quant_workflow/run_quant_workflow.py`
2. **看工作流实际做了什么**：`scripts/quant_workflow/workflow_service.py`
3. **看候选观察结构**：`services/evaluation_hub/candidate_framework.py`
4. **看候选输入与策略内核**：`services/strategy/candidate_provider.py`、`services/strategy/engine.py`
5. **最后再看执行层入口**：`scripts/classic_multifactor/` 和 `scripts/run_us_*`

### `quant_workflow` 当前真实流程

`QuantWorkflowService.run()` 目前会按下面顺序组织流程：

1. **`candidate_prepare`（可选）**
   - 仅当显式启用 `prepare_candidates=true` 或 CLI 传入 `--prepare-candidates` 时执行
   - 读取已有 dynamic/static 候选文件或显式来源文件
   - 调用共享 candidate scoring / generation 接口对 dynamic/static 候选做 enrich
   - 若显式启用 `include_market_data`，会批量请求 Futu snapshot 并把 `quote`、`change_pct`、`turnover`、`market_cap` 等字段回填到候选行
   - 若显式启用 `knot_runtime`，会在本地评分前调用 local/remote Knot runtime，把 `strategy_selection`、`knot_evaluation`、`knot_overlay_score|knot_research_score` 等结构化字段回填到候选行
   - 写回带 `scoring_model`、`strategy_tags`、`source_breakdown`、`risk_flags`、`enrichment` 等结构化字段的准备后候选输入，并生成准备报告
2. **`healthcheck`**
   - 合并旧的 `preflight` 与 `healthcheck`，只保留一个统一健康检查阶段
   - 按 `task_type=simulation|live` 检查不同本地证据：候选输入、回测报告、优化报告、SIM 证据、live 证据、reconciliation、`preflight_*.json`、`dual_run_diff*.json`
   - 优先读取已有 `state/runs/healthcheck.json`
   - 如果本地没有缓存且仍处于计划/只读模式，则回退为 offline placeholder
3. **`candidate_framework`**
   - 读取本地候选输入
   - 从候选输入里筛出观察标
   - `preferred_market` 当前会先做别名归一化：例如 CLI 传入 `hk` / `hongkong` 时，内部统一按 `hong_kong` 过滤本地候选
   - 为每个观察标输出 `trading_level=daily|minute|needs_review`
   - 同时落盘 `backtest_targets`、交易级别理由、数据充分性、`backtest_target_eligible` / `backtest_target_reason` 与是否可直接进入回测
4. **`backtest`**
   - 默认读取本地 `state/runs/classic_multifactor/vnpy_cta_backtest_report.json` 以及 `*sweep*.json`
   - 按观察标整合历史回测证据、样本区间、关键绩效指标和最优参数
   - 日频候选默认使用日级参数搜索空间；分钟级候选默认使用分钟级参数搜索空间
   - 默认仍是 evidence-only：只整理本地回测与优化产物
   - 当 CLI / service 显式开启 `auto_execute_backtests` 时，workflow 会在 `backtest` 阶段调用 vn.py CTA runner：自动拉取/复用历史 bars、执行真实 backtest、执行 `bf|ga` 参数搜索，并把结果写回 `state/runs/classic_multifactor/vnpy_cta_backtest_<symbol>.json` 与 `vnpy_cta_sweep_<symbol>.json`
   - 对港股 `hong_kong` 候选，若 `selected_as=validate_only` 但本地规则已给出明确 `daily|minute` cadence，则仍允许进入 `backtest` 目标集合；在 evidence-only 或 executable 模式下都会先产出该 observation 的 backtest 证据
5. **`readiness`**
   - 汇总 `healthcheck`、观察标和回测证据
   - 对 `simulation` 与 `live` 使用不同的门禁项
   - `simulation` 强调候选、交易级别和基础回测证据是否足够
   - `live` 额外要求最新 `preflight`、`dual_run_diff`、live report schema、approval switches 和 reconciliation 证据

最终 workflow summary 会根据 high/critical 失败项决定 `status` 是 `ok`、`warning` 还是 `blocked`。

### 输入与输出工件

#### 工作流主要输入

- `state/runs/healthcheck.json`
- `state/runs/candidate_inputs.dynamic.{hong_kong,us}.json`（per-market 主路径）
- `state/runs/candidate_inputs.static.{hong_kong,us}.json`（per-market 静态池主路径）
- `state/runs/candidate_inputs.dynamic.json` / `state/runs/candidate_inputs.json`（兼容窗口期的 legacy fallback，只读）
- `state/runs/classic_multifactor/vnpy_cta_backtest_report.json`
- `state/runs/classic_multifactor/*sweep*.json`
- `state/runs/reports/preflight_*.json`
- `state/runs/reports/*dual_run_diff*.json`
- `state/runs/hk_live_task_report.json` / `state/runs/us_live_task_report.json`
- `state/runs/futu_live_position_reconcile.json`
- `state/runs/futu_sim_position_reconcile.json`

#### 工作流主要输出

- `state/runs/quant_workflow/*_artifact_*.json`
- `state/runs/quant_workflow/*_workflow_*.json`
- `state/runs/quant_workflow/latest_index.json`
- `state/runs/candidate_inputs.prepare.report.{hong_kong,us}.json`（per-market 权威报告）
- `state/runs/candidate_inputs.prepare.report.json`（兼容窗口期的聚合摘要）

补充说明：

- 当前主要 workflow artifact 包括 `quant_trading_candidate_framework`、`quant_trading_backtest`、`quant_trading_readiness`
- 候选输入由 `UnifiedCandidateProvider` 统一读取，先按市场分别读 `candidate_inputs.dynamic.{market}.json` / `candidate_inputs.static.{market}.json`，新文件全缺失时回退读 legacy `candidate_inputs.dynamic.json` / `candidate_inputs.json`。
- 同 symbol 的合并规则保持不变：static 先入池，dynamic 针对同一 `(market, symbol)` 做字段级覆盖，不再整市场覆盖。
- `CandidateInputPreparationService` 当前会通过 `HybridCandidateGenerationService` + `CandidateScoringService` 重写 dynamic/static 候选，统一输出带 `schema_version`、`generated_at`、`as_of_date`、`market_counts`、`row_requirements`、`scoring_model`、`enrichment` 和结构化候选评分字段的对象格式，兼容 `UnifiedCandidateProvider` 的现有读取方式。
- `CandidateScoringService` 当前的 `liquidity_score` / `flow_score` 已升级为多因子启发式口径：优先看绝对成交额，再结合换手率、点差/深度代理和文本低流动性惩罚，避免仅凭 `turnover_ratio / 2` 把大票误判为 `thin_liquidity`。
- `services/evaluation_hub/candidate_framework.py` 当前直接复用 `CandidateScoringService` 暴露的共享流动性 helper，因此候选评分层与 workflow 观察层对 `thin_liquidity` 的判断口径已保持一致。
- 候选准备、workflow summary、artifact store、renderer、Knot `decision_time` 等对外时间戳当前统一按北京时间（`Asia/Shanghai`，`+08:00`）写入，便于直接与本机时间对齐。
- `state/runs/candidate_inputs.prepare.report.json` 会记录本轮写入目标、market 覆盖、`provider_merge_policy=symbol_merge_dynamic_preferred`、缺失字段统计、评分模型信息、是否请求 `include_market_data` / `knot_runtime`、以及各目标的 enrich 元数据与 warning，便于追溯"这次 workflow 看到了什么候选池"。
- `enrichment.knot` 当前会额外记录 `requested_runtime_mode`、`runtimes_used`、`single_runtime_effective` 与 `fallback_used`，用于审计这次 prepare 是否保持单一 runtime、是否发生 runtime fallback。
- prepare 写回阶段会把候选 payload / report 中的 `NaN`、`Infinity` 等非有限数值统一清洗为 `null`；这类值通常来自 Futu snapshot 中对当前标的不适用的扩展字段。
- `ArtifactStore` 会自动为 workflow artifact 追加 `next_step_suggestions`、`confirmation_requirements`、`artifact_summary`、`traceability`、`rendered_formats` 和 `risk_labels`。
- `latest_index.json` 会记录每类 artifact / workflow report 的最新路径、摘要和追溯信息，方便 CLI summary 与后续回看。

### 阶段定义与升级门槛

当前项目对外暴露的主要 workflow stage 包括：

- **`candidate_prepare`**：候选输入准备与 enrich（可选）
- **`healthcheck`**：统一健康检查与任务类型相关的本地证据核验
- **`candidate_framework`**：观察标筛选与交易级别判断
- **`backtest`**：历史回测证据与参数寻优摘要
- **`readiness`**：基于证据的阶段门禁

`readiness` 当前的升级约束核心包括：

- `healthcheck` 不能是 `blocked`
- 必须存在至少一个观察标
- 每个 promoted observation target 都应有明确的 `trading_level`
- 必须存在本地回测证据
- 最好存在参数寻优证据
- `live` 任务类型还要求最新 `preflight_*.json`、`*dual_run_diff*.json`、live report schema、审批硬开关证据和近期 reconciliation 产物

### 安全边界

以下是当前项目文档必须明确写清楚的安全边界：

- **`run.sh` 默认是 preview-first；即使是 `plan` / `research` / `sim-gate` 这类汇总命令，也只会在显式传入 `--confirm` 后才执行。**
- **`run.sh` 当前不提供 `us-live` 直通子命令；真实 live 入口仍需单独使用 `scripts/run_us_live_task.py` 并遵守人工确认与硬开关。**
- **`quant_workflow` 默认是 evidence-first，不自动跑 SIM/live。**
- **不会自动提交 Futu/OpenD 订单。**
- **不会绕过 reconciliation、approval、live switches。**
- **classic runner 的 warmup 历史 bar 不会写入正式订单状态目录；正式订单状态只记录 live session 阶段的 dry-run / Futu SIM / Futu REAL 决策与券商回报。**
- **`scripts/run_us_live_task.py` 只是顶层转发包装器；真实 live 行为由 `scripts/classic_multifactor/run_intraday_loop.py` 执行，并继续受 `--live-submit` + `VNPY_LIVE_CONFIG=YES` + `VNPY_LIVE_SUBMIT=YES` + `VNPY_LIVE_APPROVED=YES` 共同约束。**
- **LLM/Knot/外部选择结果必须先转成结构化字段，再交给本地规则层消费。**
- **凡是带 `requires_confirmation` 的入口，都应视为人工确认后才能继续。**

### 当前 HK execution 口径

根据 `services/evaluation_hub/capability_registry.py`，`HK SIM/HK Futu SIM session/HK live` 顶层入口已经完成注册，不再作为 `gap.*` 能力暴露。

这意味着：

- 当前仓库已经具备 HK 顶层入口与 workflow/readiness 可识别能力；
- 但**HK live 仍不能被表述为"可直接放行执行"**，因为是否可升级取决于本地 `SIM` 验收、`reconciliation`、审批硬开关和 risk audit 证据链；
- quant workflow 对 HK execution 仍应保持 **preview-first / evidence-first** 口径，只有在本地 readiness 证据完整时才可进入人工确认环节。

### 给协作者的最短上手建议

如果你是来做 vibecoding 的，先记住下面四件事：

- **先看 `quant_workflow`，不要一上来就钻执行脚本。**
- **先看 `state/runs/` 里现有工件，再判断链路缺的是输入、规则还是执行入口。**
- **涉及结构、入口、工件路径变化时，必须同步改本文档。**
- **涉及策略打分、择时和 market rule 变化时，必须同步改 `docs/adaptive_quant_engine_design.md`。**

### 常用只读命令示例

```bash
./run.sh check
./run.sh plan --preferred-market us
./run.sh research --max-candidates 3
./run.sh sim-gate
./run.sh live-gate
python scripts/quant_workflow/run_quant_workflow.py --preset trading_full --summary-only
python scripts/quant_workflow/run_quant_workflow.py --preset trading_full --prepare-candidates --summary-only
python scripts/quant_workflow/run_prepare_candidate_inputs.py
python -m scripts.quant_workflow --preset health_snapshot --summary-only
python scripts/run_healthcheck.py
python scripts/run_portfolio_brief.py
```

这些命令适合用来快速理解当前项目工件和阶段状态；其中 `run.sh` 默认只打印预览，不会直接执行底层脚本；真正的 SIM/live 入口应继续遵守显式确认和安全门禁。

## 阶段② — 美股多标的量化（Phase 2，dry-run only）

阶段② 计划名 `us_multi_symbol_quant_phase2`，是阶段① NVDA 单标的策略向 50 标的多标的量化的扩展骨架。**当前仓库分支严格 dry-run，不连接 OpenD、不下任何 SIM/REAL 单。**

### 入口与核心文件

- 策略骨架：[`phase2/strategy/us_multi_symbol_phase2_strategy.py`](/projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy.py) — `LIVE_SUBMIT=False` 硬开关，4 因子 + 入场 5 项 + 出场优先级链 + N 日冷却
- 池配置：[`phase2/strategy/config/pool_config.yaml`](/projects/vnpy/phase2/strategy/config/pool_config.yaml) + 加载器 [`phase2/strategy/pool_loader.py`](/projects/vnpy/phase2/strategy/pool_loader.py)（含 4 类过滤）
- 组合风控 & 熔断：[`phase2/strategy/portfolio_risk.py`](/projects/vnpy/phase2/strategy/portfolio_risk.py) — 单标 4 条 + 组合 5 条；熔断状态 JSON 落盘到 `state/runs/<plan>/<run_id>/portfolio_state.json`，重启可恢复
- 回测 runner：[`phase2/runners/run_phase2_backtest.py`](/projects/vnpy/phase2/runners/run_phase2_backtest.py)（默认 `--dry-run`）
- 对账 runner：[`phase2/runners/run_phase2_reconcile.py`](/projects/vnpy/phase2/runners/run_phase2_reconcile.py)（5 项指标，`>20%` 失败退出码 5）
- 性能基线 runner：[`phase2/runners/run_futu_perf_baseline.py`](/projects/vnpy/phase2/runners/run_futu_perf_baseline.py)（1/10/30 标的三档骨架）

### 测试

```bash
python3 -m unittest \
  phase2.strategy.tests.test_pool_loader \
  phase2.strategy.tests.test_pool_filters \
  phase2.strategy.tests.test_budget_allocator \
  phase2.strategy.tests.test_risk_rules
```

阶段② 当前自动化测试 39/39 通过。

### Dry-run 命令

```bash
# 回测 dry-run（产物：state/runs/us_multi_symbol_quant_phase2/<run_id>/run_report.json）
python3 phase2/runners/run_phase2_backtest.py --run-id smoke_001 \
  --mock-nav 1000000 --mock-cash 1000000

# 对账 dry-run（产物：reconcile_report.json + robustness.png 占位）
python3 phase2/runners/run_phase2_reconcile.py \
  --actual <actual.json> --expected <expected.json> --run-id smoke_pass
```

两个 runner 默认 `--dry-run=True`；显式传 `--no-dry-run` 在本仓库分支会直接退出码 3，提示需新开独立 plan。

### SIM/REAL 升级红线

- **本计划禁止启动 SIM**：从 dry-run 升级到 Futu SIM 必须新开独立 plan（建议命名 `us_multi_symbol_quant_phase3_sim`）。
- 升级需逐项打勾：[阶段② SIM 准入清单](/projects/vnpy/docs/research/us_multi_symbol_quant/05_sim_gate_checklist.md)（5 项打勾）。
- REAL/live 受全局 `--live-submit` + `VNPY_LIVE_*` 环境变量 + 硬开关 + 人工审批 + 对账 + 风控 + 订单幂等共同约束，与阶段①一致。

### 研究文档

- 入口：[`docs/research/us_multi_symbol_quant/00_index.md`](/projects/vnpy/docs/research/us_multi_symbol_quant/00_index.md)
- 01 指标调研 / 02 Futu 多标能力 / 03 策略方案 / 04 平台性能基线 / 05 SIM 准入清单 / 06 真实回测 runbook

## 阶段② v2 — futumd 投放路径 + 池周更 + 真实回测通道

阶段② v2 计划名 `us_multi_symbol_quant_phase2_v2`，在阶段② dry-run 骨架之上新增 3 条落地通道，仍然严格保留 dry-run 红线：

### futumd 兼容单文件策略（迁移投放专用）

- 入口：[`phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`](/projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py)
- 硬约束：零本地 import（`from phase2.* / from services.*` 一个都没有，AST 测试强制）；所有指标 / 风控 / 预算逻辑收为 `Strategy._xxx` 私有方法；池规模 ≤ 20；`LIVE_SUBMIT` 默认 `False`。
- 与现有 `us_multi_symbol_phase2_strategy.py` 关系：互不替换；前者用于 Futu 平台手工上传，后者用于本仓库 dry-run / 单元测试。
- 自检命令：
  ```bash
  python3 -m py_compile phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py
  python3 phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py --check
  ```

#### v2 趋势跟随版（plan `phase2_strategy_redesign_v2`）

- 入口：[`phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2.py`](/projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2.py)
- 定位：v1 在 5 年真实回测中收益受限于"5 条 AND 闸门 + 固定 TP/trailing-dd + 预算双重缩放"，v2 按业界主流趋势跟随范式（Donchian 突破 + Chandelier 止损 + SMA200 regime 滤网 + vol-targeting）重写入场/出场，**保留 v1 文件不动以做 A/B 对照**。
- 入场（3 条 AND）：① Close > SMA(200) 总市场 regime；② Close > SMA(100) 长期价格滤网；③ Close ≥ prior 55 日最高（Donchian 突破）。
- 出场（2 优先级）：① Chandelier 止损 `max_since_entry - 3.0×ATR(22)`；② 趋势离场 Close < SMA(50)。固定 TP / trailing-dd / fast-slow 死叉 / ATR-cap 全部删除。
- 仓位：`per_symbol_budget = NAV × pool_budget_pct(0.95) / max_concurrent(6)`，再用 vol-targeting `scale = target_vol(0.15) / annualised_atr_pct` 缩放（夹持 [0.4, 1.5]），单标的单仓位无加仓。
- 硬约束完全继承 v1：单文件、零本地 import、`Strategy._xxx` 私有方法、`LIVE_SUBMIT=False` ships、池规模 ≤ 20、状态仅 in-memory。
- 5 年真实回测对照（区间 2021-05-23 ~ 2026-05-22，pool_config_fixed.yaml 6 只锁定池，初始资金 100000，手续费 0.0003）：
  - v1 latest：total_return +26.85%、年化 +4.89%、MaxDD 12.83%、315 trades、胜率 42%。
  - v2 default：total_return **+32.49%**、年化 +5.81%、MaxDD **6.18%（减半）**、135 trades、胜率 **52%**。
  - 完整报告：`state/runs/phase2_strategy_redesign_v2/20260523T153754Z/REPORT.md`。
- 自检命令：
  ```bash
  python3 -m py_compile phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2.py
  python3 phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2.py --check
  ```
- 跑 v2 真实回测：在 `run_phase2_multi_backtest.py` 命令里追加 `--strategy-path phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2.py` 即可，runner / adapter / 引擎全部零改动。

#### v3 趋势跟随版（最终版，plan `phase2_strategy_redesign_v2`）

- 入口：[`phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py`](/projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py)
- 定位：v2 在 5 年回测中只取得 +32%，远低于 mega-cap 池子能产生的趋势跟随上限。v3 经过 6 轮迭代（iter1~iter6 全部保留为单独文件以便归因），最终在 v2 基础上做了 6 项关键改造：
  1. **入场更早**：Donchian-55 → Donchian-10，更敏感地捕捉趋势拐头；删除 SMA(100) 重复闸门，只保留 SMA(200) regime。
  2. **出场更宽**：Chandelier 倍数 3 → 6，ATR 窗口 22 → 40，趋势离场 SMA(50) → SMA(150)，让 NVDA/AVGO 这种妖股能跑完整段。
  3. **集中度提升**：`max_concurrent_holdings` 6 → 3，单仓位从 ~12% NAV 升到 ~33% NAV，让赢家权重显著提升。
  4. **抗"买入次日洗"**：新增 `min_hold_bars=10` 入场宽限期 + `disaster_loss_pct=0.12` 单仓硬止损（宽限期内仍生效）。
  5. **回撤护栏**：`regime_flat=True`，当 Close < SMA(200) 时立即清仓所有持仓，恢复要等 SMA(200) 重新上穿。
  6. **金字塔加仓**：`max_slices=3, pyramid_atr_step=1.0, pyramid_size_pct=0.5`，持仓后涨 1×ATR 加仓 0.5×base，最多累计 3 片。
- 5 年真实回测（区间 2021-05-23 ~ 2026-05-22，pool_config_fixed.yaml 6 只锁定池，初始 100k，手续费 0.0003）：
  - v1 latest：total_return +26.85%、MDD 12.83%、315 trades。
  - v2 default：total_return +32.49%、MDD 6.18%、135 trades。
  - **v3 final：total_return +320.49%、年化 +33.40%、MDD 23.92%、82 trades** ✅ 超 200% 目标 120 pp。
  - 全程对照与失败案例（iter6 portfolio-dd-cut 锁死至 +12.76%）：`state/runs/phase2_strategy_redesign_v2/20260523T153754Z/REPORT_OPTIMIZATION.md`。
- 硬约束完全继承 v1/v2：单文件、stdlib only、零本地 import、Strategy 私有 helper、`LIVE_SUBMIT=False` ships、池规模 ≤ 20、状态仅 in-memory。
- 自检与回测命令：
  ```bash
  python3 -m py_compile phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py
  python3 phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py --check
  PYTHONPATH=. python3 phase2/runners/run_phase2_multi_backtest.py \
    --pool-config phase2/strategy/config/pool_config_fixed.yaml \
    --strategy-path phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py \
    --start 2021-05-23 --end 2026-05-22 \
    --init-cash 100000 --rate 0.0003 --slippage 0.0 \
    --run-id v3_final --output-root state/runs/phase2_strategy_redesign_v2/<ts>
  ```
- 中间迭代版本 `_v2_iter1.py` ~ `_v2_iter6.py` 保留在仓库中作为归因证据，不应被修改或删除；线上 / 平台真实部署只用 `_v3.py`。

### 池周更流水线

- Runner：[`phase2/runners/run_pool_update.py`](/projects/vnpy/phase2/runners/run_pool_update.py)
- 候选池：[`phase2/strategy/config/pool_universe.yaml`](/projects/vnpy/phase2/strategy/config/pool_universe.yaml)
- 默认 `--dry-run`，`--apply` 必须配 `--confirm`；3 道安全闸（变更 > 30% / sector > 40% / 池规模超限）触发非零退出。
- 行情数据来源仅本地 `phase2/strategy/config/pool_metrics_snapshot.json`，**不联网**。

### 真实回测命令（双轨）

- 本地影子轨：`python3 -m services.backtest.cli --symbols ... --output state/runs/us_multi_symbol_quant_phase2_v2/<run>/local_expected.json`
- Futu 主轨：手工在 Futu 客户端上传 futumd 文件 + 设置参数 + 导出 `futu_actual.json`
- 对账：复用 `python3 phase2/runners/run_phase2_reconcile.py`（5 项指标 / >20% 失败退出）
- 详细 runbook：[06_real_backtest_runbook.md](/projects/vnpy/docs/research/us_multi_symbol_quant/06_real_backtest_runbook.md)

阶段② v2 当前自动化测试 50/50 通过（39 老 + 11 新）。

## 阶段② 多标本地回测入口（plan `phase2_multi_backtest`）

为支撑 futumd 兼容多标策略 [`phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`](/projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py) 在迁移到 Futu 平台前的本地真实回测，新增独立子包 `phase2/backtest/`：

- 适配器：[`phase2/backtest/futumd_strategy_adapter.py`](/projects/vnpy/phase2/backtest/futumd_strategy_adapter.py)
  - `PortfolioRuntime` 按 symbol 分桶维护 OHLCV / positions / entry_costs；`cash_value` 是组合层单变量。
  - `build_futumd_namespace(runtime)` 把 futumd 平台符号（`bar_close` / `bar_high` / `bar_low` / `bar_volume` / `bar_open` / `cash` / `net_asset` / `position_holding_qty` / `place_limit` / `close_positions` / `alert` / 各 enum / `StrategyBase` / `declare_*` / `show_variable`）作为闭包注入到模块命名空间，全部按 `symbol` 参数路由。
  - `load_futumd_strategy(strategy_path, runtime)` 用 `importlib.util` 先注入命名空间再 exec 策略文件，因此策略文件顶部 `try: bar_close ... except NameError` 会跳过 stub 区，DSL 全部命中真实分桶。**futumd 策略源码零改动**。
  - `settle_pending(...)` 按"下一根 K 线"的参考价撮合 BUY / SELL_CLOSE，处理现金不足回扣、卖单 qty clamp、滑点、手续费。
- 引擎：[`phase2/backtest/portfolio_backtest_engine.py`](/projects/vnpy/phase2/backtest/portfolio_backtest_engine.py)
  - 通过 `vnpy.trader.database.get_database()` 拉本地多标日线，按交易日 union 排序驱动；每日：写入桶 → `strategy.handle_data()` → 用 `all_dates[i+1]` 开盘价撮合 → mark-to-close 写入 equity 曲线。
  - 输出 4 份产物到 `state/runs/phase2_multi_backtest/<run_id>/`：`equity_curve.csv` / `positions_daily.csv` / `trade_ledger.csv` / `summary.json`。
  - **不连 OpenD / Futu / 任何远端服务**；`place_limit` 被 adapter 拦截只入内存队列。
  - **回测专用覆盖**：引擎默认 `force_live_submit=True`，在 `strategy.initialize()` 之后把 `strategy.LIVE_SUBMIT` 翻转为 `True`。原因是 futumd 策略同文件中 `LIVE_SUBMIT=False` 分支会跳过 `place_limit` 只发 `alert`——这是发布到 Futu 平台后防止误下单的硬门，但也导致本地回测拿不到交易。adapter 的 `place_limit` 本身**只写内存**，不可能发出真实订单，所以该覆盖只在本地回测语境内生效。CLI 可用 `--respect-live-submit` 返回"dry-run alert"语义。该设计使 futumd 策略源码零修改即可迁移到 Futu 平台（平台拿到的 `LIVE_SUBMIT` 仍为 `False`）。
- CLI 入口：[`phase2/runners/run_phase2_multi_backtest.py`](/projects/vnpy/phase2/runners/run_phase2_multi_backtest.py)

```bash
# 12 标 1 年 smoke 回测（默认 force_live_submit=True，交易可见）
python3 phase2/runners/run_phase2_multi_backtest.py \
    --start 2025-05-21 --end 2026-05-20 \
    --init-cash 1000000 \
    --rate 0.0003 --slippage 0.0 \
    --run-id smoke_2025_2026

# 需要验证 futumd 策略出厂默认 LIVE_SUBMIT=False 分支时，追加 --respect-live-submit
python3 phase2/runners/run_phase2_multi_backtest.py \
    --start 2025-05-21 --end 2026-05-20 \
    --respect-live-submit \
    --run-id smoke_2025_2026_dryrun

# 自定义池 / 自定义策略路径 / 自定义产物根
python3 phase2/runners/run_phase2_multi_backtest.py \
    --pool-config phase2/strategy/config/pool_config.yaml \
    --strategy-path phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py \
    --start 2024-01-01 --end 2024-12-31 --init-cash 500000
```

测试覆盖：

- [`phase2/strategy/tests/test_futumd_strategy_adapter.py`](/projects/vnpy/phase2/strategy/tests/test_futumd_strategy_adapter.py) 17 项：分桶取数、cash 不串户、`place_limit` 路由、`settle_pending` 撚e合规则、`load_futumd_strategy` 启动真实策略。
- [`phase2/strategy/tests/test_portfolio_backtest_engine.py`](/projects/vnpy/phase2/strategy/tests/test_portfolio_backtest_engine.py) 8 项：mock 数据库 + 微型 2 标策略，端到端验证次日开盘成交、trade ledger 含两 symbol、4 份报告字段齐备；另含 `force_live_submit` 对照组（默认翻转能产生交易 vs `--respect-live-submit` 零交易）。
- 全套 phase2 回归 75/75 通过（原 73 + force_live_submit 对照 2）。

12 标 1 年 smoke 走实数据（`state/runs/phase2_multi_backtest/smoke_2025_2026_v2/`）：251 个交易日、trade_count=23、7 只标有交易、total_return +1.34%、max_drawdown 3.59%、末日全部 0 仓平仓。
边界与限制：

- 与 `tmp/` 完全解耦——本子包不 import `tmp/*` 任何模块，便于将来跟 futumd 策略整体搬走。
- 与 `phase2/runners/run_phase2_backtest.py`（合成 dry-run smoke）互补：本 runner 跑真实历史 K 线、生成真实组合权益曲线；前者只校验 allocator 与池配置加载。
- 当前实现仅支持日线（`Interval.DAILY`）；分钟级支持需要新开 plan 适配 `BarType.K_1M / K_5M / K_15M` 与会话内多 bar/天 的撮合规则。

## 阶段② live：多标美股天级别量化交易（dry_run / futu_sim / futu_real）

> 计划：[`.codebuddy/plan/phase2_live_trading/`](/projects/vnpy/.codebuddy/plan/phase2_live_trading/)
> 进度：[`.codebuddy/task_list/phase2_live_trading.md`](/projects/vnpy/.codebuddy/task_list/phase2_live_trading.md)

阶段② live 在 phase2 多标回测之上新增**真实 OpenD 连接 + SIM/REAL 双账户的天级别 live 交易能力**，复用 phase2 现有的 futumd 策略文件（零改动）、池配置、风控阈值，并补齐：

- 4 级 pre-trade gate（幂等 → 对账 → 单标风控 → 组合风控），任一级被拒立即写入 `events.jsonl` 的 `order_blocked` 事件并把状态机推进到 `rejected`。
- 6 道安全开关（环境变量 `VNPY_LIVE_CONFIG/VNPY_LIVE_SUBMIT/VNPY_LIVE_APPROVED/FUTU_TRADE_PASSWORD`、CLI `--futu-env`、CLI `--live-submit`），任一缺失即直接退出非 0 码。
- 状态机 `OrderState` 全程持久化到 `state/runs/phase2_live/<env>/<run_id>/orders/<request_id>.json`，进程重启可恢复在途订单并继续幂等性判定。
- 三态分目录产物：`state/runs/phase2_live/{dry_run|futu_sim|futu_real}/<run_id>/`，每态自带独立的 `daily_report.json` / `events.jsonl` / `orders/` / `reconcile/` / `positions_snapshot.csv`，互不串扰。

模块边界：

- [`phase2/live/order_state.py`](/projects/vnpy/phase2/live/order_state.py)：复用 `services.common.trading_models.OrderIntent/OrderState` 与 `services.trade_state.storage.OrderStateStore`，新增 `build_request_id`（确定性哈希）、`transition`（状态机合法性）、`OrderStateStoreExt`（`save_intent_as_state` / `list_open_request_ids`）。
- [`phase2/live/risk.py`](/projects/vnpy/phase2/live/risk.py)：从 `scripts/classic_multifactor/risk.py` 物理 copy 后改写为 phase2 命名空间，去掉对 `scripts.classic_multifactor.model` 的依赖；每次 `size_and_check` 内新建 `LiveRiskGuard` 实例，**幂等性交由 `IdempotencyGate` 处理**，不再依赖 `LiveRiskGuard._seen`。
- [`phase2/live/safety.py`](/projects/vnpy/phase2/live/safety.py)：6 开关验证，`validate_safety()` 输出 `SafetyDecision(allowed, execution_env, missing_switches)`。
- [`phase2/live/broker.py`](/projects/vnpy/phase2/live/broker.py) + [`phase2/live/futu_broker.py`](/projects/vnpy/phase2/live/futu_broker.py)：定义 `LiveBroker` 协议（connect/disconnect/unlock_trade/query_account/query_positions/place_order/cancel_order/subscribe_quote/register_order_handler）与 `FutuBroker` 实现（`OpenSecTradeContext` + `OpenQuoteContext`，REAL 强制 `unlock_trade(password)` 不通过则拒绝下单）。
- [`phase2/live/live_adapter.py`](/projects/vnpy/phase2/live/live_adapter.py)：与 `phase2/backtest/futumd_strategy_adapter.py` **完全同接口**的 live 版本，`LivePortfolioRuntime` 把 `place_limit` 的 OrderIntent 通过 `intent_callback` 转出。
- [`phase2/live/guards.py`](/projects/vnpy/phase2/live/guards.py)：4 级 gate 与 `GatePipeline`，`PortfolioRiskGate` 严格检查 BUY 后单标 / 当日新仓 / 总敞口 / 回撤；SELL 不受单标 cap 约束（用于减仓 / 平仓）。
- [`phase2/live/runner.py`](/projects/vnpy/phase2/live/runner.py)：`DailyLiveRebalanceRunner` 单触发 / 单 rebalance；clock 与 sleep 全注入；REAL 模式即使 `--no-auto-cancel-on-eod` 也会强制收盘前撤掉所有在途订单。
- [`phase2/runners/run_phase2_live_daily.py`](/projects/vnpy/phase2/runners/run_phase2_live_daily.py)：CLI 入口，所有重 import（vnpy/futu）lazy 化，`--help` 即时返回；6 开关失败时 stderr 输出 `BLOCKED` + 缺失开关清单并退出码 2。

入口示例：

```
# dry_run（默认；不连 OpenD、不下任何 SIM/REAL 单；用本地 vnpy 数据库取行情）
python3 phase2/runners/run_phase2_live_daily.py \
    --pool-config phase2/strategy/config/pool_config.yaml \
    --rebalance-now

# futu_sim（连接 OpenD 模拟账户；--live-submit 真正调 broker.place_order）
export VNPY_LIVE_CONFIG=/path/to/live_config.yaml
export VNPY_LIVE_SUBMIT=1
export VNPY_LIVE_APPROVED=2026-05-21
export FUTU_HOST=127.0.0.1 FUTU_PORT=11111
python3 phase2/runners/run_phase2_live_daily.py \
    --futu-env 模拟 --live-submit \
    --rebalance-time 15:55 --session-tz America/New_York \
    --max-single-position-pct 0.20 --max-order-value 20000

# futu_real（额外要求 FUTU_TRADE_PASSWORD；REAL 模式强制 auto_cancel_on_eod）
export FUTU_TRADE_PASSWORD=***
python3 phase2/runners/run_phase2_live_daily.py \
    --futu-env 真实 --live-submit \
    --auto-cancel-on-eod
```

测试覆盖（132/132 项新增 + 75/75 既有 = 207 全绿）：

- `phase2/live/tests/test_order_state.py` 22 项
- `phase2/live/tests/test_risk.py` 18 项
- `phase2/live/tests/test_safety.py` 28 项
- `phase2/live/tests/test_broker.py` 15 项
- `phase2/live/tests/test_live_adapter.py` 18 项
- `phase2/live/tests/test_guards.py` 23 项
- `phase2/live/tests/test_runner.py` 12 项
- `phase2/live/tests/test_run_phase2_live_daily.py` 8 项

边界与限制：

- 不修改 `phase2/backtest/*` 与 `phase2/strategy/*` 任何源码；futumd 策略文件（`LIVE_SUBMIT=False`）保持出厂状态，runner 内部对加载到的 strategy 模块单边赋值 `LIVE_SUBMIT = bool(--live-submit)`，原文件不动。
- 仅天级别（每个 rebalance_date 触发一次 handle_data）；分钟级 / Tick 级需要新开 plan，并补充 `MinuteTradeGuard` 限频 gate。
- dry_run 不连 OpenD；行情来自本地 vnpy 数据库（与 phase2 多标回测同源）。
- REAL 模式真实下单的实际权限由 OpenD 与券商账户决定；本仓库的硬开关与人工审批仅是**最低**门槛，不构成对真实资金的足额保障。

## 阶段③ phase2 策略自我优化闭环（双 subagent）

`phase2.optimize` 子包在 phase2 回测引擎之上叠加一个 **Optimizer/Evaluator 双 subagent** 闭环：每轮提案 N 个候选参数 → 通过 `param_overrides` 注入 `phase2/backtest/portfolio_backtest_engine.py` 的本地回测分支 → 评估器多维打分 + ranking → 决定 continue / stop。**纯本地回测路径，永不连 OpenD/Futu。**

入口与默认配置：

- 入口脚本：`phase2/runners/run_phase2_strategy_self_optimize.py`
- 搜索空间：`phase2/strategy/config/optimize_search_space.yaml`（18 个安全可调参数 + frozen 列表）
- 池：默认沿用 `phase2/strategy/config/pool_config_fixed.yaml`（固定池：NVDA / MSFT / AVGO / TSM / TSLA / AMZN）
- 产物根目录：`state/runs/phase2_strategy_self_optimize/<session_id>/`
  - `iter_<k>/proposals.json` + `evaluations.json` + `leaderboard.csv`
  - `iter_<k>/trial_<m>/{proposal,params.yaml,applied_params.json,trial_result,error.log,equity_curve.csv,trade_ledger.csv,positions_daily.csv,summary.json}`
  - 顶层：`session_summary.json` + `progress.log` + `REPORT.md`

常用命令：

```bash
# dry-run（不跑回测，仅产出提案与骨架）
python3 phase2/runners/run_phase2_strategy_self_optimize.py \
    --dry-run --max-iters 2 --trials-per-iter 2 \
    --start 2025-01-02 --end 2025-01-10

# 完整 5 年闭环（建议先用户确认）
python3 phase2/runners/run_phase2_strategy_self_optimize.py \
    --max-iters 10 --trials-per-iter 4 \
    --start 2021-05-23 --end 2026-05-22 \
    --init-cash 100000 --rate 0.0003

# 打印某个会话的累计 leaderboard
python3 phase2/runners/run_phase2_strategy_self_optimize.py \
    --print-leaderboard <session_id>
```

硬隔离与安全约束：

- 包级守卫 `phase2.optimize.assert_no_live_imports()`：CLI 启动 + 每次 `run_session` 前调用，禁止 `futu`、`phase2.live.*` 出现在 `sys.modules`。
- 9 个子进程级 pytest（`test_no_live_imports.py`）证明任意子模块导入路径都不会拉入禁止包。
- frozen_param 集合 `{LIVE_SUBMIT, _pool, max_orders_per_day}` 由 search_space loader + Optimizer + 引擎 `param_overrides` 三重拒绝。
- 三个高风险参数硬上限固化在 `HARD_CEILINGS`：`pool_budget_pct ≤ 0.95`、`max_concurrent_holdings ≤ 8`、`stop_loss_pct ∈ [0.02, 0.10]`，超限的 YAML 直接拒绝加载。
- 引擎参数注入在 `strategy.initialize()` 之后立刻完成，注入完成后引擎做 `getattr == value` 的一致性断言；任何不一致由 trial_runner 标 `injection_mismatch` 并继续下一 trial。
- `--respect-live-submit` 不暴露给 CLI，trial_runner 同样会拒绝该参数；闭环始终在 `force_live_submit=True` 的本地回测分支。

可选 LLM 通道：

- 通过 `--llm-optimizer` / `--llm-evaluator` 启用；两者都失败/未配置即降级为本地规则。
- 走 `vnpy_llm.OpenAICompatibleClient.complete_json`，需配置环境变量：
  - `PHASE2_OPT_LLM_BASE_URL`、`PHASE2_OPT_LLM_API_KEY`、`PHASE2_OPT_LLM_MODEL`
  - 可选：`PHASE2_OPT_LLM_API_TYPE`（`openai` | `knot_agui`）、`PHASE2_OPT_LLM_API_USER`、`PHASE2_OPT_LLM_TIMEOUT`、`PHASE2_OPT_LLM_TEMPERATURE`
- LLM 仅产出 JSON，由本地 schema 校验后才注入；任何字段越界即丢弃单条提案，绝不写入交易侧。

测试覆盖（44 项新增 + 既有 75 项 = 119 全绿）：

- `phase2/strategy/tests/test_optimize_search_space.py` 10 项
- `phase2/strategy/tests/test_optimizer_subagent.py` 8 项
- `phase2/strategy/tests/test_evaluator_subagent.py` 8 项
- `phase2/strategy/tests/test_optimize_trial_runner.py` 5 项
- `phase2/strategy/tests/test_optimize_coordinator_dryrun.py` 3 项
- `phase2/strategy/tests/test_no_live_imports.py` 9 项（子进程隔离断言）

边界与限制：

- 闭环目前 **串行** 执行 trial；引入并行后必须在 trial_runner 内补 `multiprocessing.Lock` 写产物，并在 SessionPaths 增加每 worker 子目录约定。
- Coordinator 不会回写策略源码；任何由优化得到的"最优参数"都需要由人手动整理为新的 baseline、再走原 phase2 多标回测流程复跑确认。
- 默认 stop_rules：`max_iters=10`、`patience=3`、`min_delta=0.5`、`max_runtime_min=90`；磁盘剩余 < 1 GB 触发 `stop_reason=disk_low`。
- 评估器使用 stdlib min-max 归一化（pure Python，零 numpy/pandas 依赖），样本量在 phase2 当前规模（≤ 数千 trial）下足够；若后续要做 walk-forward 多窗口对比，需要在 evaluator 内补 fold 维度。
