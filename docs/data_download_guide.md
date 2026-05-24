# 项目数据下载与刷新指南

### 文档定位

本文档只保留当前仓库真正相关的数据下载/刷新路径，服务于 `phase2` 候选准备、回测前准备和 Futu/OpenD 相关行情富化。
不再覆盖上游 `community/`、`elite/` 中的大量通用 UI 说明。

### 一句话理解

当前项目里与数据下载最相关的路径主要有两类：

- **`phase2` 候选准备中的行情富化**：由 `scripts/quant_workflow/run_prepare_candidate_inputs.py` 触发，核心是通过 OpenD 拉取 snapshot / universe 相关数据，写入 `state/runs/candidate_inputs.*`。
- **通用历史数据进库**：通过 vn.py / DataManager / 数据服务把历史 K 线写入本地数据库，供回测和策略初始化使用。

### 1. `phase2` 候选准备里的数据拉取

项目当前最常用的数据刷新入口是：

```bash
python3 scripts/quant_workflow/run_prepare_candidate_inputs.py --market us --strategy knot_first --include-market-data
```

这个入口会做几件事：

- 根据 `--market` 选择 `us` / `hong_kong` / `all`
- 根据 `--strategy` 选择 `knot_first` / `score_first` / `merge_existing`
- 在开启 `--include-market-data` 时，通过 OpenD 拉取 market snapshot 等实时市场数据
- 对候选做 enrich 和评分后，把结果写回 `state/runs/`

#### 主要产物

- `state/runs/candidate_inputs.dynamic.us.json`
- `state/runs/candidate_inputs.dynamic.hong_kong.json`
- `state/runs/candidate_inputs.prepare.report.us.json`
- `state/runs/candidate_inputs.prepare.report.hong_kong.json`
- 聚合兼容摘要：`state/runs/candidate_inputs.prepare.report.json`

#### 使用建议

- 如果目标是刷新 `phase2` 候选池，优先用这个入口，而不是手工改 JSON。
- 如果只是验证参数或排查逻辑，优先先看 [system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md) 中对应入口说明。
- 若 OpenD snapshot 全部失败，当前流程会保守处理，避免用零富化结果覆盖已有动态池。

### 2. TED / Knot 研究流程里的数据依赖

`TED` 三阶段入口位于：

- `phase2/ted/run_knot_4dim_research.py`
- `phase2/ted/run_candidate_preparation.py`
- `phase2/ted/run_ted_discovery.py`
- `phase2/ted/run_ted_full_pipeline.py`

其中第二阶段本质上仍会复用候选准备和市场数据 enrich 逻辑，因此同样依赖 OpenD / snapshot 数据。

当前 `TED` 结果归档到：

```text
state/runs/ted/YYYYMMDDTHHMMSS/
```

详见 [system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md) 和 `phase2/ted/` 下对应说明文档。

### 3. 通用历史数据下载（数据库）

如果目标不是刷新 `phase2` 候选，而是为回测/初始化准备历史 K 线，则应走 vn.py 的历史数据入库路径：

- 通过 `DataManager` / 数据服务下载历史数据
- 或从 CSV 导入历史数据
- 或通过录制方式写入数据库

这些数据随后会被：

- `scripts/classic_multifactor/run_vnpy_cta_backtest.py`
- `scripts/classic_multifactor/run_intraday_loop.py`
- `scripts/classic_multifactor/run_daily_rebalance.py`

等入口复用。

### 4. 何时用哪条路径

- **想刷新候选池 / 给 `phase2` 补市场数据**：用 `scripts/quant_workflow/run_prepare_candidate_inputs.py`
- **想跑 `TED` / `Knot` 三阶段并保留日期归档**：用 `phase2/ted/` 下入口
- **想准备回测用历史 K 线数据库**：走 vn.py / DataManager / 数据服务历史数据下载

### 5. 相关文档

- [adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md)
- [system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)
- [knot_agent_prompt_design.md](/projects/vnpy/docs/knot_agent_prompt_design.md)
- [remote_knot_batch_integration.md](/projects/vnpy/docs/remote_knot_batch_integration.md)
