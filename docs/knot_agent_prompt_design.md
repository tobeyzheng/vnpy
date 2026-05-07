# Knot Agent 调用逻辑与 Prompt 设计（v2）

## 设计原则
不是把 knot agent 降级成弱规则分类器，而是：
- 保留其默认 MarketResearchScreenerAgent 能力
- 只补充必要的结构化输出约束
- 仅在特定任务类型追加 task_planning 限制与策略规则

## Prompt 结构
### 1. Base Prompt
保留默认角色：
- 新闻搜集
- 宏观风险识别
- 股票/ETF 筛选
- 结构化信号生成

### 2. JSON Output Supplement
统一补充：
- 只能输出纯 JSON 对象
- 禁止 Markdown
- 禁止 JSON 前后解释文字
- 必须符合 schema

### 3. Planning Guard Supplement
仅在以下场景追加：
- strategy_select
- rule_match
- json_only_classifier
- 或显式不允许 task_planning 的调用

### 4. Strategy Rules Supplement
仅在策略选择类任务追加，不污染普通 research/evaluation 任务。

## 调用原则
- 研究/评估类任务：保留 agent 强能力，可允许 task_planning（若确有多步需求）
- 简单 JSON 分类类任务：优先本地规则，不默认调用 knot agent
- 若调用 knot agent，必须经过 schema 校验；失败则 fallback

## 目标
既避免 task_planning 滥用和格式漂移，又不压制 knot agent 本来的研究与筛选能力。
