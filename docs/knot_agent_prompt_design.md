# Knot Agent 调用逻辑与 Prompt 设计（v1）

## 目标
解决以下问题：
1. 简单结构化策略选择任务误用 task_planning
2. 输出不是纯 JSON，破坏下游自动解析
3. 策略选择规则不稳定，偏保守或漂移
4. 角色定义与实际“策略选择器”场景不匹配

## 调用原则
### 允许调用 knot agent 的场景
- 需要对候选标的做额外主观判断（agent_judgment）
- 输入不是单只标的单次规则匹配，而是候选池排序/补充理由
- 需要结构化 JSON 结果回写到 evaluation_signals.json

### 禁止调用 / 降级场景
- 用户只要求单次明确策略选择，且输入字段完整
- 用户要求严格 JSON 输出，且当前任务可由本地规则直接完成
- 缺少关键输入字段，不适合让 knot agent自由发挥

## task_planning 约束
- 默认禁止 task_planning
- 仅当任务满足以下全部条件时允许：
  1. 涉及多标的、多来源搜集
  2. 需要分步骤执行与汇总
  3. 不是单次策略分类
- 对于 strategy_select / rule_match / json_only_classifier 类任务，强制 `use_task_planning=false`

## JSON 输出约束
System Prompt 必须包含以下硬约束：
- 必须且只能输出一个纯 JSON 对象
- 禁止 Markdown 代码块
- 禁止 JSON 前后额外解释
- JSON 必须符合指定 schema
- 若信息不足，也必须返回合法 JSON

## 推荐角色定义
你是港股/美股策略选择器，不是泛化研究助手。
你的任务是根据给定结构化输入，在预定义枚举中选择唯一策略，并输出严格 JSON。

## 策略选择优先级（建议固化）
1. `risk_score > 0.7` 或 `capital_score < 0.3` -> `block_trade`
2. 缺少 3 个以上关键字段 -> `watch_only`
3. `rsi > 80` 且 `trend_score >= 0.8` -> `pullback_buy`
4. `rsi > 80` 且 `trend_score < 0.8` -> `watch_only`
5. 接近阻力位且有明确事件催化 -> `breakout_momentum`
6. 趋势健康 + `rsi < 70` + 均线多头 -> `trend_following`
7. 其他 -> `watch_only`

## 建议输出 schema
{
  "strategy": "trend_following|breakout_momentum|pullback_buy|watch_only|block_trade",
  "confidence": 0.0,
  "reason": "string",
  "risk_flags": ["string"],
  "use_task_planning": false,
  "format_ok": true
}

## 工程实现建议
- 在本地先做 `should_call_knot_agent()` 判断
- 对 JSON-only 任务，优先本地规则引擎
- knot agent 仅作为补充判断，不做默认第一跳
- 对 knot agent 返回值增加 schema 校验；不合法则丢弃
