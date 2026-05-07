# 自适应量化策略引擎设计（港股 + 美股）

## 目标
1. 由 knot agent 动态生成 candidate inputs，而不是长期依赖固定文件。
2. 同时适配港股、美股两套市场规则。
3. 明确 raw_score 的来源、计算与使用位置。
4. 细化更可用的交易成本模型。
5. 将买入/卖出时机升级为「规则约束 + AI 驱动」。
6. 形成完整的自适应量化策略引擎闭环。

## 当前确认
- 当前交易主流程优先读取 `state/runs/candidate_inputs.json`
- `raw_score` 在现有主流程中被直接消费，不在主流程内生产
- `vnpy_llm/scripts/analyze_stock_once.py` 中存在原始评分公式：
  - `raw_score = 0.45 + 0.12 * bullish - 0.14 * bearish + 0.25 * (trend_score - 0.5) - 0.25 * (risk_score - 0.5)`
  - 然后裁剪到 `[0,1]`
- 需要进一步确认该脚本是否已在当前候选生成链被自动调用；当前主流程未见直接调用痕迹

## 目标架构

### 1. Universe Discovery（全市场自主挖掘）
- 港股 Universe
- 美股 Universe
- 候选发现来源：
  - knot agent（宏观/事件/主题/行业/个股）
  - 技术扫描器（趋势、突破、回踩）
  - 资金/流动性过滤器
  - 基本面质量过滤器

### 2. Candidate Generation（动态候选生成）
统一输出到 candidate input 协议：
- symbol
- market
- name
- rationale
- risk
- raw_score
- confidence_source
- action_hint
- signals[]

优先级：
1. 动态生成文件 `state/runs/candidate_inputs.dynamic.json`
2. 若不存在，再回退到 `state/runs/candidate_inputs.json`

### 3. Raw Score Engine
建议统一拆成：
- trend_score
- momentum_score
- flow_score
- quality_score
- event_score
- risk_penalty

建议公式：
`raw_score = clip(w1*trend + w2*momentum + w3*flow + w4*quality + w5*event - w6*risk_penalty, 0, 1)`

补充：保留兼容模式，允许接入 `analyze_stock_once.py` 的 legacy score 作为一个输入特征，而不是最终唯一 raw_score。

### 4. Entry Timing（买点）
规则候选：
- trend_following
- breakout_momentum
- pullback_buy
- watch_only
- block_trade

规则约束：
- 港股/美股最小交易单位
- tick size
- 可交易时段
- 预算/仓位/行业暴露
- drawdown 风控

AI 输出：
- strategy
- confidence
- reason
- risk_flags
- optional: entry_timing / invalidators / size_multiplier

### 5. Exit Timing（卖点）
组合：
- 固定止损（如 -8%）
- 固定止盈（如 +15%）
- 跟踪止盈
- 趋势转弱退出
- 事件失效退出
- AI 风险升级退出

### 6. Cost Model（真实交易成本）
港股：
- commission
- platform_fee
- settlement_fee
- stamp_duty
- slippage
- tick_size 校验

美股：
- commission（若有）
- SEC/TAF 等费项（预留）
- slippage
- 简化 tick size

### 7. Reconciliation（对账）
- 本地账本 vs Futu 模拟盘持仓
- 本地下单意图 vs Futu 可卖数量
- 订单状态迁移：submitting / submitted / partial_filled / filled / rejected / canceled

## 分阶段实施

### Phase A
- 动态 candidate 生成脚本（依赖 knot agent）
- HK / US 市场配置骨架
- raw score 统一配置
- entry/exit timing 规则骨架

### Phase B
- 批量远程 knot 编排执行器
- 多市场统一主流程
- 订单状态机
- 持仓对账自动化

### Phase C
- regime detection
- 仓位自适应
- AI + rules 融合决策器
