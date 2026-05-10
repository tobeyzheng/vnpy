# 自适应量化引擎项目文档（港股 + 美股）

### 文档定位

这份文档不是历史方案草稿，而是给项目协作者快速理解当前策略内核的**项目文档**。
重点回答三个问题：

1. 当前策略链路到底已经实现了什么；
2. 候选输入、评分、择时、策略选择之间如何衔接；
3. 哪些部分仍然只是扩展点或能力缺口，不能被误写成“已经自动化完成”。

### 文档同步规则

- 当 `services/strategy/` 下的入口、核心字段、评分公式、market rule、候选输入路径发生变化时，必须同步更新本文档。
- 当 `scripts/quant_workflow/` 改变了候选读取方式、阶段含义或 strategy integration 方式时，也要同步回写本文档。
- 如果代码和文档冲突，以代码为准；但下一次相关修改必须把文档追平。
- 对外说明当前策略能力时，应优先引用本文档，而不是继续沿用旧的设计草稿表述。

### 一句话理解当前引擎

当前实现是一条“**候选输入 → 结构化特征 → `raw_score` → 入场/退出择时 → 策略选择 → `StrategySignal`**”的确定性策略链。
它适合被回测、仿真、workflow 解释层复用，但**不是**一个可以绕过风控和审批直接自治下单的系统。

### 当前核心模块

- **`scripts/run_hk_sim_task.py` / `scripts/run_hk_futu_sim_session.py` / `scripts/run_hk_live_task.py`**：新增 HK 顶层包装入口，统一把 HK `SIM` / `session` / `live` 接到 vnpy intraday 主线，并保持 preview-first / evidence-first 的安全边界。
- **`services/strategy/candidate_scoring.py`**：定义可复用的候选评分接口、dynamic/static 混合评分模型，以及单标候选 enrich 能力。
- **`services/strategy/candidate_enrichment.py`**：定义可复用的候选 enrich 层，负责可选接入真实市场快照与 Knot 结构化评估。
- **`services/strategy/candidate_generation.py`**：基于评分接口生成 dynamic/static 候选 payload，也可单独评估一个候选行。
- **`services/strategy/candidate_preparation.py`**：调用生成服务重写候选输入，并补充 `row_requirements`、准备元信息和追溯报告。
- **`services/strategy/candidate_provider.py`**：统一读取和标准化候选输入。
- **`services/strategy/raw_score.py`**：定义 `RawScoreFeatures` 和 `RawScoreEngine`。
- **`services/strategy/timing.py`**：定义入场和退出择时规则。
- **`services/strategy/strategy_selector.py`**：根据结构化因子做确定性策略选择和阻断。
- **`services/strategy/engine.py`**：把候选、行情、事件催化、外部选择结果整合成 `StrategyEvaluation`，并复用候选评分接口。
- **`services/strategy/market_rules.py`**：给港股/美股提供基础的市场参数。
- **`services/strategy/registry.py`**：定义当前默认 `strategy_id`、可交易动作和最低 `raw_score` 门槛。

### 当前真实数据流

#### 1. Candidate input 层

候选输入目前仍由 `UnifiedCandidateProvider` 负责读取：

- 动态文件：`state/runs/candidate_inputs.dynamic.json`
- 静态文件：`state/runs/candidate_inputs.json`

但在 provider 之前，当前已经不是“只清洗旧 JSON”，而是由一条**生成 + enrich + 收敛**链路负责产出候选：

- `CandidateMarketDataService` 可选批量接入真实 Futu snapshot，并回填 `quote` / `change_pct` / `turnover` / `market_cap`
- `CandidateKnotEnrichmentService` 可选调用 local/remote Knot runtime，并回填 `strategy_selection` / `knot_evaluation` / `knot_overlay_score|knot_research_score`
- `CandidateScoringService` 提供可复用的 candidate scoring 接口
  - `dynamic`：`DynamicCandidateScoreModel`
  - `static`：`StaticCandidateScoreModel`
- `HybridCandidateGenerationService` 基于上面的 enrich + 评分接口：
  - 生成完整 dynamic/static payload
  - 或单独评估一个候选 row
- `CandidateInputPreparationService` 调用生成服务，统一写回 dynamic/static 文件，并输出准备报告

对应入口保持不变，但现在可显式开启 enrich：

- 可由 `scripts/quant_workflow/run_prepare_candidate_inputs.py` 独立触发
  - `--include-market-data`
  - `--knot-runtime off|local|remote|auto`
  - 默认 `knot_runtime=auto`，优先尝试 remote，初始化不可用时退回 local
- 也可由 `scripts/quant_workflow/run_quant_workflow.py --prepare-candidates` 在 workflow 前置触发
  - `--prepare-include-market-data`
  - `--prepare-knot-runtime off|local|remote|auto`
  - 默认 `prepare_knot_runtime=auto`
- 会输出 `state/runs/candidate_inputs.prepare.report.json`
- 会把 dynamic/static 统一重写为带 `schema_version`、`generated_at`、`as_of_date`、`selection_policy`、`market_counts`、`row_requirements`、`scoring_model` 与 `enrichment` 的对象格式
- 对外时间戳当前统一写为北京时间（`Asia/Shanghai`，`+08:00`），包括 prepare report、workflow summary、artifact、renderer 和 Knot `decision_time`
- `enrichment.knot` 当前会显式记录 `requested_runtime_mode`、`runtimes_used`、`single_runtime_effective` 与 `fallback_used`，用于追溯这次 enrich 是否保持单一 runtime、是否发生 runtime fallback

当前 provider 行为要点：

- 如果指定了 `market`，会在 merged 候选池中筛选该市场；不再以“整市场是否存在于 dynamic”决定是否丢弃 static。
- 如果没有指定 `market`，会先读 static，再把 dynamic 针对相同 `(market, symbol)` 的候选做字段级覆盖。
- 这意味着当前优先级已经改为**按 `(market, symbol)` 精细 merge**，dynamic 对同 symbol 具有覆盖权，但不会再整市场覆盖。
- 所有候选在返回前都会经过 `normalize_symbol()` 标准化。

这点和旧文档里“主流程优先读取静态文件”的说法不同；当前实现已经是**动态文件优先**。

当前生成/准备层会额外补齐或规范化的候选字段包括：

- `candidate_type`
- `confidence_source`
- `generated_at`
- `as_of_date`
- `theme_bucket`
- `risk_level`
- `consensus_score`
- `strategy_tags`
- `strategy_votes`
- `source_breakdown`
- `risk_flags`
- `explanation_summary`
- `explanation_ready`
- `research_note`
- `data_completeness`
- `max_signal_score`
- `scoring`

因此当前候选输入已经不再只是“上游随意写入的 JSON”，而是可以先经过一次本地混合评分生成，再被 workflow 和 strategy 层消费。

#### 2. Candidate scoring 接口与 `RawScoreFeatures` 层

当前新增了一层可复用的 candidate scoring 接口：

- `CandidateScoringService.score_row(row, mode="dynamic|static")`
- `CandidateScoringService.enrich_row(row, mode="dynamic|static")`
- `HybridCandidateGenerationService.evaluate_single_candidate()`

这层的作用是：

- 把候选生成与单标评估统一到一套评分输入上
- 给 dynamic/static 维护不同的评分模型，但共享相同接口
- 在进入 `StrategyEngine` 前先补齐 `strategy_tags`、`source_breakdown`、`risk_flags`、`scoring` 等结构化字段

当前 dynamic/static 评分模型分别是：

- **dynamic**：`dynamic_hybrid_candidate_v2`
  - `trend_score`
  - `relative_strength_score`
  - `flow_score`
  - `event_score`
  - `quality_score`
  - `liquidity_score`
  - `regime_fit_score`
  - `knot_overlay_score`
  - `risk_penalty`
- **static**：`static_hybrid_candidate_v2`
  - `quality_score`
  - `stability_score`
  - `liquidity_score`
  - `sector_leadership_score`
  - `trend_health_score`
  - `valuation_score`
  - `knot_research_score`
  - `explanation_ready_score`
  - `risk_penalty`

`StrategyEngine.evaluate_candidate()` 现在会先调用 candidate scoring 接口 enrich 候选，再把其中一部分字段映射到 `RawScoreFeatures`。

当前 `RawScoreFeatures` 仍包含以下字段：

- `trend_score`
- `momentum_score`
- `flow_score`
- `quality_score`
- `event_score`
- `risk_penalty`
- `legacy_score`

在 `StrategyEngine.evaluate_candidate()` 中，这些字段的当前来源变为：

- **`trend_score`**：优先使用 enrich 后的 `trend_score`
- **`momentum_score`**：优先使用 enrich 后的 `relative_strength_score`，并和 `quote.change_pct` 推导值做兼容
- **`flow_score`**：优先使用 enrich 后的 `flow_score`，并和 `quote.turnover / flow_divisor` 推导值做兼容
- **`quality_score`**：优先使用 enrich 后的 `quality_score`
- **`event_score`**：优先使用 enrich 后的 `event_score`，若存在明确催化则提高下限
- **`risk_penalty`**：优先使用 enrich 后的 `risk_penalty`，若当日涨跌幅过大则提高下限
- **`legacy_score`**：保留候选原始 `raw_score` 作为兼容输入

#### 3. `raw_score` 计算层

`RawScoreEngine` 当前权重如下：

- `trend_score`: `0.24`
- `momentum_score`: `0.18`
- `flow_score`: `0.16`
- `quality_score`: `0.16`
- `event_score`: `0.14`
- `risk_penalty`: `0.12`

当前基础公式可以理解为：

```text
base = 0.24*trend + 0.18*momentum + 0.16*flow + 0.16*quality + 0.14*event - 0.12*risk_penalty
```

如果存在 `legacy_score`，还会按 `legacy_weight = 0.35` 进行融合：

```text
final = clip((1 - 0.35) * base + 0.35 * legacy_score, 0, 1)
```

最终分数会被裁剪到 `[0, 1]` 并保留四位小数。

这说明当前实现已经不再是“只有一个旧 `raw_score` 被直接透传”，而是一个**新评分 + 旧评分兼容混合**的模型。

#### 4. 入场择时层

`EntryTimingEngine.decide()` 当前会返回以下动作之一：

- `watch_only`
- `breakout_momentum`
- `trend_following`
- `pullback_buy`

当前决策主要基于：

- `trend_score`
- `rsi`
- 是否存在事件催化
- 是否临近阻力位
- 均线结构是否偏多

它输出的是 `TimingDecision`，包含：

- `action`
- `reason`
- `confidence`
- `invalidator`
- `suggested_size_pct`

#### 5. 策略选择层

`StrategySelector` 是当前真正的“规则裁决器”，它的职责不是生成市场观点，而是把结构化特征收敛成可执行或不可执行的策略选择。

当前重要规则包括：

- `risk_score > 0.7`：直接 `block_trade`
- `capital_score < 0.3`：直接 `block_trade`
- `missing_fields >= 3`：降级为 `watch_only`
- 高 RSI 且趋势很强：只允许 `pullback_buy`
- 临近关键位且存在催化：可选 `breakout_momentum`
- 趋势、均线、评分都健康：可选 `trend_following`
- 其余情况：回退到 `watch_only` 或择时 fallback

也就是说，**外部 AI/LLM/Knot 结果不能直接越过这个规则层下单**。

#### 6. 外部策略选择的并入方式

`StrategyEngine` 支持 `external_strategy_selection`，但当前合并逻辑是保守的：

- 外部结果如果是 `watch_only` 或 `block_trade`，会直接阻断交易。
- 只有在本地规则已经允许交易的前提下，外部选择结果才有机会替换策略 ID。
- 如果本地规则不允许交易，外部结果也不能强行打开交易。

这条规则非常关键：**AI 只能提供结构化建议，不能绕过本地规则和安全边界。**

#### 7. 最终信号输出层

`StrategyEngine.evaluate_candidate()` 和 `StrategyEngine.evaluate_bar()` 最终返回 `StrategyEvaluation`，其中最重要的是 `signal`：

- `strategy_id`
- `symbol`
- `market`
- `direction`
- `score`
- `confidence`
- `allow_trade`
- `target_position_pct`
- `reason`
- `risk_flags`
- `metadata`

是否允许交易，当前要同时满足：

- `raw_score >= 0.55`
- 选择结果 `allow_trade = true`
- 最终动作属于可交易动作：
  - `trend_following`
  - `pullback_buy`
  - `breakout_momentum`

默认 `strategy_id` 由 `StrategyRegistry` 提供，当前是 `raw_score_timing_v1`。

### 退出逻辑当前实现

`ExitTimingEngine.decide()` 当前已经实现的是规则型退出逻辑：

- `pnl_pct <= -0.08`：`stop_loss`
- `pnl_pct >= 0.15`：`take_profit`
- RSI 很高且趋势边际转弱：`trim_or_exit`
- `risk_score >= 0.75`：`reduce_risk`
- 否则：`hold`

因此，当前退出层已经有可运行的基础逻辑，但仍偏轻量，适合作为统一默认规则，而不是最终完备的仓位管理系统。

### 港股 / 美股市场规则当前状态

`get_market_rules()` 目前提供的是轻量级 market profile：

#### 港股 `hong_kong`

- `currency = HKD`
- `lot_size_mode = dynamic`
- `default_stop_loss_pct = 0.08`
- `default_take_profit_pct = 0.15`
- `slippage_bps = 8`

#### 美股 `us`

- `currency = USD`
- `lot_size_mode = unitary`
- `default_stop_loss_pct = 0.07`
- `default_take_profit_pct = 0.18`
- `slippage_bps = 5`

要注意：

- 这里只是基础参数，不是完整交易制度数据库。
- 当前层里**还没有**每只港股的真实手数表、tick size 表、完整费项表。
- 更精细的最小交易单位、券商费率、税费、交易时段约束，仍需要依赖更下层或外部数据源补足。

### 当前已经实现的能力

- **动态候选输入优先于静态候选输入**
- **候选输入前置准备与 schema 收敛报告**
- **HK / US 的基础 market rule 抽象**
- **多维 `raw_score` 计算与 legacy 分数兼容**
- **入场择时与退出择时**
- **规则型策略选择与风险阻断**
- **外部选择结果的保守合并机制**
- **输出统一的 `StrategySignal` / `StrategyEvaluation` 结构**

### 当前仍然是缺口或扩展点的部分

- **动态 universe discovery 还不是这层自动完成的**
  - 当前仍依赖上游把候选写入 `state/runs/candidate_inputs.dynamic.json`
- **当前 prepare/provider 仍按 market 粗覆盖，不是按 symbol 精细 merge**
  - 如果 dynamic 只提供某市场的局部补丁，仍可能整体遮掉 static 的同市场候选
- **真实市场数据与 Knot 接入已支持为可选 enrich，但默认并不自动开启**
  - 需要显式启用 CLI / workflow 参数才会尝试请求 snapshot 或调用 Knot runtime
  - Futu SDK 不可用、远端 Knot 未配置或 schema 校验失败时，会降级为 warning / fallback，而不是替代本地安全规则层
- **Knot 批处理不是这层直接调度的**
  - 当前更接近“逐候选结构化 enrich”，而不是完整远端批处理编排器
- **完整的对账与订单状态机不属于这层职责**
  - 这些能力在更靠近 execution/trade_state 的层里
- **market rule 仍然偏轻量**
  - 还没有完整 tick size、税费、lot size 明细
- **当前评分特征仍偏启发式**
  - 更细的 quality / event / microstructure 特征可以继续扩展
- **当前覆盖重点仍是 US 与 HK**
  - 其他市场还未纳入 beginner-safe 主线

### 对协作者最重要的扩展原则

如果你要继续扩展这条链路，建议遵守下面的顺序：

1. **优先补输入结构，不要直接绕过引擎。**
   - 新的 AI/规则输出应该先转成结构化字段，再进入 `StrategyEngine`
2. **优先扩展 `RawScoreFeatures`，不要把逻辑散落到多个脚本。**
3. **外部选择只能影响策略，不应直接影响真实下单。**
4. **新增 market 时，要同时改 market rule、候选标准化、capability 文档和本文档。**
5. **涉及候选路径、字段、策略动作变化时，必须同步更新本文档。**

### 协作者推荐阅读顺序

如果你要改这一层，建议按下面顺序阅读源码：

1. `services/strategy/candidate_provider.py`
2. `services/strategy/engine.py`
3. `services/strategy/raw_score.py`
4. `services/strategy/timing.py`
5. `services/strategy/strategy_selector.py`
6. `services/strategy/market_rules.py`
7. `services/strategy/registry.py`

### 结论

当前“自适应量化引擎”已经有**可运行的评分、择时、策略选择骨架**，并且已经和上层 workflow / candidate input 体系接上。
但它仍然应该被描述为：

- 一个**确定性、可解释、可继续扩展**的策略内核；
- 一个适合被 research / backtest / simulation 复用的中间层；
- 而不是一个已经完成全自动 candidate discovery、全自动对账、全自动实盘执行的闭环系统。
