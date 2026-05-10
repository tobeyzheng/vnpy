## 项目系统集成指南

### 文档定位

这份文档面向在本仓库内做 vibecoding、接手开发、补充脚本或维护集成链路的协作者。
目标不是重复 `docs/community/` 或 `docs/elite/` 的上游说明，而是快速说明**本项目自定义层**的真实入口、关键目录、状态产物、阶段边界和已知缺口。

### 文档同步规则

- 当项目结构、主入口脚本、默认参数、状态产物路径、阶段定义发生变化时，必须同步更新本文档和 `docs/adaptive_quant_engine_design.md`。
- 新增、删除、重命名入口脚本时，文档更新应与代码变更在同一轮提交中完成。
- 如果代码与文档不一致，以代码为准；发现偏差后，下一次相关修改必须补齐文档。
- 对其他协作者来说，这两份文档是理解项目的第一入口，不要让它们长期停留在“设计草稿”状态。

### 一句话理解当前项目主线

当前项目不是单一策略脚本，而是一条“**工作流编排 → 研究说明 → 候选观察 → 回测校验 → 阶段门禁 → 仿真/实盘入口隔离**”的多层结构。
其中，推荐的第一阅读入口是：

1. `scripts/quant_workflow/run_quant_workflow.py`
2. `scripts/quant_workflow/workflow_service.py`
3. `services/evaluation_hub/`
4. `services/strategy/`
5. `scripts/classic_multifactor/`

### 关键目录与职责

- **`scripts/quant_workflow/`**：统一的 beginner quant 工作流入口与编排层。负责把研究、候选、回测校验和个人计划串起来。
- **`services/evaluation_hub/`**：项目级解释与计划生成层。负责能力地图、研究文档、候选观察、阶段 readiness、artifact 落盘。
- **`services/strategy/`**：策略内核层。负责候选输入标准化、`raw_score` 计算、入场/退出择时、策略选择与信号生成。
- **`scripts/classic_multifactor/`**：经典多因子主线。包含 LLM research、vn.py CTA backtest、日内/日频执行入口。
- **`services/execution_guard/`、`services/risk_engine/`、`services/trading_pipeline/`**：执行保护层。负责 live gate、precheck、reconciliation、risk guard、sim/live task 约束。
- **`services/futu_account/`、`services/futu_opend/`、`services/futu_sim_trade/`**：券商与 OpenD 接入层。
- **`state/runs/`**：运行时工件目录。健康检查、候选输入、回测报告、workflow artifact、orders、brief 等都落在这里。

### 主要入口脚本

- **`scripts/quant_workflow/run_quant_workflow.py`**：当前推荐的总入口。
  - 默认 `--mode plan`
  - 默认 `--stage research`
  - 默认 `--workflow beginner_quant`
  - 默认不会自动启动 SIM/live 脚本
- **`scripts/run_healthcheck.py`**：环境和账户健康检查入口，输出 `state/runs/healthcheck.json`。
- **`scripts/run_portfolio_brief.py`**：组合摘要入口，聚合 HK/US close report 与 healthcheck，输出 `state/runs/portfolio_brief.json`。
- **`scripts/classic_multifactor/run_vnpy_cta_backtest.py`**：官方 vn.py CTA 回测入口，输出 `state/runs/classic_multifactor/vnpy_cta_backtest_report.json`。
- **`scripts/classic_multifactor/run_intraday_loop.py`**：分钟级主线 runner，带执行保护，属于 simulation/live 邻近入口。
- **`scripts/classic_multifactor/run_daily_rebalance.py`**：日频再平衡 runner，带执行保护，属于 simulation/live 邻近入口。
- **`scripts/run_us_sim_task.py`**：US SIM 任务入口。
- **`scripts/run_us_futu_sim_session.py`**：US Futu SIM session 入口。
- **`scripts/run_us_live_task.py`**：US live task 入口，默认仍应保持显式人工确认。

### 推荐的项目阅读顺序

如果你是第一次接触本项目，建议按下面顺序阅读：

1. **看总入口**：`scripts/quant_workflow/run_quant_workflow.py`
2. **看工作流实际做了什么**：`scripts/quant_workflow/workflow_service.py`
3. **看能力边界和缺口定义**：`services/evaluation_hub/capability_registry.py`
4. **看阶段门禁**：`services/evaluation_hub/readiness_gate.py`
5. **看候选输入与策略内核**：`services/strategy/candidate_provider.py`、`services/strategy/engine.py`
6. **最后再看执行层入口**：`scripts/classic_multifactor/` 和 `scripts/run_us_*`

### `quant_workflow` 当前真实流程

`QuantWorkflowService.run()` 目前会按下面顺序组织流程：

1. **`healthcheck`**
   - 优先读取已有 `state/runs/healthcheck.json`
   - 如果本地没有缓存且仍处于计划/研究模式，则回退为 offline placeholder
2. **`capability_map`**
   - 读取本地 capability registry
   - 汇总 stage capability、stage boundary map、available stages、已知 capability gaps
3. **`research`**
   - 生成 beginner research artifact
   - 由 `services/evaluation_hub/doc_renderer.py` 同时输出可读 Markdown/JSON 内容到 artifact 中
4. **`candidate_framework`**
   - 读取本地候选输入
   - 生成 `beginner_watchlist` / `observe_only` / `validate_only` 三类观察结果
5. **`backtest_validation`**
   - 读取本地 vn.py 回测报告
   - 标准化 sample period、fees、slippage、stability metrics、data quality notes
6. **`planning`**
   - 生成个人 beginner plan、risk budget、phase/task、readiness checklist
7. **`execution_boundary`**
   - 如果本地发现 simulation/live 能力入口，只输出“需要明确确认”的边界警告，不会自动执行

最终 workflow summary 会根据 readiness 的 high/critical 失败项决定 `status` 是 `ok` 还是 `blocked`。

### 输入与输出工件

#### 工作流主要输入

- `state/runs/healthcheck.json`
- `state/runs/candidate_inputs.dynamic.json`
- `state/runs/candidate_inputs.json`
- `state/runs/classic_multifactor/vnpy_cta_backtest_report.json`

#### 工作流主要输出

- `state/runs/quant_workflow/*_artifact_*.json`
- `state/runs/quant_workflow/*_workflow_*.json`
- `state/runs/portfolio_brief.json`

补充说明：

- 候选输入由 `UnifiedCandidateProvider` 统一读取。
- `candidate_inputs.dynamic.json` 的优先级高于 `candidate_inputs.json`，但当前合并规则是**按 market 覆盖**，不是按 symbol 精细合并。
- `ArtifactStore` 会自动为 workflow artifact 追加 next step suggestions 和 confirmation requirements。

### 阶段定义与升级门槛

当前项目对外暴露的主要 stage 包括：

- **`research`**：研究说明、术语解释、证据整理
- **`backtest`**：本地回测元数据标准化和校验
- **`simulation`**：仿真前的 readiness 可视化与边界提示
- **`live`**：实盘邻近能力可视化与严格门禁
- **`orchestration`**：统一 workflow 入口本身

`ReadinessGateService` 当前的升级约束核心包括：

- `health_status` 不能是 `blocked`
- 必须存在 research artifact
- simulation/live 相关阶段必须有 backtest metadata
- 必须存在明确的 risk budget
- 必须有 review notes
- live 阶段还要求 capability gaps 被消除

### 安全边界

以下是当前项目文档必须明确写清楚的安全边界：

- **`quant_workflow` 默认是 plan-first，不自动跑 SIM/live。**
- **不会自动提交 Futu/OpenD 订单。**
- **不会绕过 reconciliation、approval、live switches。**
- **LLM/Knot/外部选择结果必须先转成结构化字段，再交给本地规则层消费。**
- **凡是带 `requires_confirmation` 的入口，都应视为人工确认后才能继续。**

### 当前已知 capability gaps

根据 `services/evaluation_hub/capability_registry.py`，以下入口仍被显式标记为缺口：

- `scripts/run_hk_sim_task.py`
- `scripts/run_hk_futu_sim_session.py`
- `scripts/run_hk_live_task.py`

这意味着：

- 当前仓库可以讨论 HK workflow、候选、研究和规划；
- 但**不能把 HK execution 说成已经有完整自动化入口**；
- beginner workflow 对 HK execution 仍应保持 manual / planned-only 口径。

### 给协作者的最短上手建议

如果你是来做 vibecoding 的，先记住下面四件事：

- **先看 `quant_workflow`，不要一上来就钻执行脚本。**
- **先看 `state/runs/` 里现有工件，再判断链路缺的是输入、规则还是执行入口。**
- **涉及结构、入口、工件路径变化时，必须同步改本文档。**
- **涉及策略打分、择时和 market rule 变化时，必须同步改 `docs/adaptive_quant_engine_design.md`。**

### 常用只读命令示例

```bash
python scripts/quant_workflow/run_quant_workflow.py --stage research --mode plan
python scripts/run_healthcheck.py
python scripts/run_portfolio_brief.py
```

这些命令适合用来快速理解当前项目工件和阶段状态；真正的 SIM/live 入口应继续遵守显式确认和安全门禁。
