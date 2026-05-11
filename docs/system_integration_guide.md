## 项目系统集成指南

### 文档定位

这份文档面向在本仓库内做 vibecoding、接手开发、补充脚本或维护集成链路的协作者。
目标不是重复 `docs/community/` 或 `docs/elite/` 的上游说明，而是快速说明**本项目自定义层**的真实入口、关键目录、状态产物、阶段边界和已知缺口。

### 文档同步规则

- 当项目结构、主入口脚本、默认参数、状态产物路径、阶段定义发生变化时，必须同步更新本文档和 `docs/adaptive_quant_engine_design.md`。
- 新增、删除、重命名入口脚本时，文档更新应与代码变更在同一轮提交中完成。
- 如果代码与文档不一致，以代码为准；发现偏差后，下一次相关修改必须补齐文档。
- 对其他协作者来说，这两份文档是理解项目的第一入口，不要让它们长期停留在“设计草稿”状态。
- 框架、需求、入口等改动完成后，还应同步检查 [project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) 是否需要补历史记录。

### 协作治理补充

- 已开始或已完成的 plan item / task item，应及时更新对应 markdown 状态，避免任务记录长期滞后。
- 对框架、需求、入口、规则、运行产物结构、阶段边界有影响的改动，应同步记录到 [project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)。
- 在回复、文档、日志和说明中，避免暴露真实账户金额、完整账号、密钥、令牌等敏感信息；需要表达时应使用脱敏后的描述。

### 一句话理解当前项目主线

当前项目不是单一策略脚本，而是一条“**候选准备 → 健康检查 → 观察标筛选 → 回测证据 → readiness 门禁 → 仿真/实盘入口隔离**”的多层结构。
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

### 主要入口脚本

- **`run.sh`**：仓库根目录的安全统一入口脚本。
  - 默认只做 preview，不执行底层命令
  - 仅在显式传入 `--confirm` 时才真正执行目标入口
  - 当前封装的子命令包括 `check`、`plan`、`research`、`sim-gate`、`live-gate`、`backtest`、`us-sim`
  - 会在执行前打印解析后的命令、是否触达 OpenD / 账户 / SIM 状态、预期输出文件和交易影响说明
  - 当前**不暴露** `us-live` 直通命令，避免把真实 live 入口误包装成“一键执行”
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
  - 默认读取并重写 `state/runs/candidate_inputs.dynamic.json` 与 `state/runs/candidate_inputs.json`
  - 当前会先通过 `HybridCandidateGenerationService` 生成/补齐 dynamic/static 候选，再由 `CandidateInputPreparationService` 统一写回
  - 可选通过 `--include-market-data` 批量补 Futu snapshot 字段，通过 `--knot-runtime off|local|remote|auto` 批量补 Knot 结构化评估
  - 若未显式传 `--knot-runtime`，当前默认使用 `auto`：优先尝试 remote，初始化不可用时退回 local
  - 输出 `state/runs/candidate_inputs.prepare.report.json`
  - 写回前会递归清洗非有限数值；来自 snapshot 或其他 enrich 源的 `NaN` / `Infinity` 会统一落为 `null`，保证产物保持严格 JSON
  - 可通过 `--dynamic-source` / `--static-source` 指定替代输入源
- **`python -m scripts.quant_workflow`**：与上面的脚本入口等价的模块入口，适合统一的一键工作流触发。
- **`scripts/run_healthcheck.py`**：环境和账户健康检查入口，输出 `state/runs/healthcheck.json`。
- **`scripts/run_portfolio_brief.py`**：组合摘要入口，聚合 HK/US close report 与 healthcheck，输出 `state/runs/portfolio_brief.json`。
- **顶层 `scripts/` 入口兼容约束**：凡是从仓库根目录通过 `python scripts/...` 或被 `run.sh` 直接调用的顶层脚本，都应在文件开头先解析 `REPO_ROOT` 并注入 `sys.path`，避免 `from services...` 这类仓库内导入在直接执行时失效。
- **`scripts/classic_multifactor/run_vnpy_cta_backtest.py`**：官方 vn.py CTA 回测入口，输出 `state/runs/classic_multifactor/vnpy_cta_backtest_report.json`。
- **`scripts/classic_multifactor/run_intraday_loop.py`**：分钟级主线 runner，带执行保护，属于 simulation/live 邻近入口。
  - 共享 `BaseRunner.map_vt_symbol()` 会在会话启动前把 classic config 中的美股 `NVDA.US` 规范化为 `NVDA.SMART`，并把港股 `00700.HK` 规范化为 `00700.SEHK`，避免 vn.py/Futu 会话因交易所后缀不匹配而无法创建策略实例。
  - classic strategy 的 `on_init() -> load_bar()` warmup 历史 bar 当前只用于指标/模型预热，不再通过 execution hook 写入正式 `OrderStateStore`；初始化阶段出现的历史信号不会污染正式 dry-run / Futu 模拟 / Futu 实盘订单目录。
- **`scripts/classic_multifactor/run_daily_rebalance.py`**：日频再平衡 runner，带执行保护，属于 simulation/live 邻近入口。
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
- `state/runs/candidate_inputs.dynamic.json`
- `state/runs/candidate_inputs.json`
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
- `state/runs/candidate_inputs.prepare.report.json`

补充说明：

- 当前主要 workflow artifact 包括 `quant_trading_candidate_framework`、`quant_trading_backtest`、`quant_trading_readiness`
- 候选输入由 `UnifiedCandidateProvider` 统一读取。
- `candidate_inputs.dynamic.json` 的优先级仍高于 `candidate_inputs.json`，但当前合并规则已经改为**按 `(market, symbol)` 精细合并**：static 先入池，dynamic 针对同一 symbol 做字段级覆盖，不再整市场覆盖。
- `CandidateInputPreparationService` 当前会通过 `HybridCandidateGenerationService` + `CandidateScoringService` 重写 dynamic/static 候选，统一输出带 `schema_version`、`generated_at`、`as_of_date`、`market_counts`、`row_requirements`、`scoring_model`、`enrichment` 和结构化候选评分字段的对象格式，兼容 `UnifiedCandidateProvider` 的现有读取方式。
- 候选准备、workflow summary、artifact store、renderer、Knot `decision_time` 等对外时间戳当前统一按北京时间（`Asia/Shanghai`，`+08:00`）写入，便于直接与本机时间对齐。
- `state/runs/candidate_inputs.prepare.report.json` 会记录本轮写入目标、market 覆盖、`provider_merge_policy=symbol_merge_dynamic_preferred`、缺失字段统计、评分模型信息、是否请求 `include_market_data` / `knot_runtime`、以及各目标的 enrich 元数据与 warning，便于追溯“这次 workflow 看到了什么候选池”。
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
- 但**HK live 仍不能被表述为“可直接放行执行”**，因为是否可升级取决于本地 `SIM` 验收、`reconciliation`、审批硬开关和 risk audit 证据链；
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
