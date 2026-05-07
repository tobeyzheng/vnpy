from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from services.evaluation_hub.models import EvaluationSignal


BASE_SYSTEM_PROMPT = """
你是 MarketResearchScreenerAgent，负责为量化交易系统执行新闻搜集、宏观风险识别、股票/ETF 筛选和结构化信号生成。

你必须基于输入证据和可查询数据工作，不得编造事实，不得直接下单，不得绕过风控。你需要重点关注美联储态度、美国经济趋势、美债收益率、日元汇率/套息交易风险、VIX、行业事件、个股新闻、财报、资金流和技术趋势。

当需要筛选股票时，按流动性、趋势、估值、财务质量、成长性、新闻情绪、事件强度、宏观敏感度和风险水平进行综合评分。当需要分析已知股票时，结合行情、K线、财务、资金、新闻、公告、研报和估值进行判断。当证据不足或风险升高时，必须保守处理。

只能使用 decision_time 之前可见的信息。输出必须是严格 JSON。
""".strip()


JSON_ONLY_SUPPLEMENT = """
补充约束：
- 必须且只能输出一个纯 JSON 对象
- 禁止使用 Markdown 代码块
- 禁止在 JSON 前后添加任何解释性文本
- JSON 必须严格符合指定字段和枚举值
- 若证据不足，也必须返回合法 JSON，而不是输出解释文字
""".strip()


PLANNING_GUARD_SUPPLEMENT = """
任务规划约束：
- 对于单次策略选择、规则匹配、JSON 分类任务，禁止使用 task_planning
- 仅当任务涉及多标的、多来源搜集、分步骤汇总时，才允许 task_planning
""".strip()


STRATEGY_RULES_SUPPLEMENT = """
策略选择优先级（从上到下匹配）：
1. risk_score > 0.7 或 capital_score < 0.3 -> block_trade
2. 缺少3个以上关键字段 -> watch_only
3. RSI > 80 且 trend_score >= 0.8 -> pullback_buy
4. RSI > 80 且 trend_score < 0.8 -> watch_only
5. 接近阻力位 + 有明确事件催化 -> breakout_momentum
6. 趋势健康 + RSI < 70 + 均线多头 -> trend_following
7. 其他 -> watch_only
""".strip()


@dataclass
class KnotAgentPromptSpec:
    task_type: str = 'evaluation'
    json_only: bool = True
    allow_task_planning: bool = False
    require_schema_validation: bool = True
    system_prompt: str = ''


def build_system_prompt(task_type: str, allow_task_planning: bool) -> str:
    parts = [BASE_SYSTEM_PROMPT, JSON_ONLY_SUPPLEMENT]
    if task_type in {'strategy_select', 'rule_match', 'json_only_classifier'}:
        parts.append(PLANNING_GUARD_SUPPLEMENT)
        parts.append(STRATEGY_RULES_SUPPLEMENT)
    elif not allow_task_planning:
        parts.append(PLANNING_GUARD_SUPPLEMENT)
    return '\n\n'.join(parts)


def should_call_knot_agent(task_type: str, fields_present: int, need_multi_step: bool = False) -> bool:
    if task_type in {'strategy_select', 'rule_match', 'json_only_classifier'}:
        return False
    if not need_multi_step and fields_present >= 5:
        return False
    return True


class KnotAgentEvaluationAdapter:
    def prompt_spec(self, task_type: str = 'evaluation', fields_present: int = 0, need_multi_step: bool = False) -> KnotAgentPromptSpec:
        allow = should_call_knot_agent(task_type, fields_present, need_multi_step)
        return KnotAgentPromptSpec(
            task_type=task_type,
            json_only=True,
            allow_task_planning=allow and need_multi_step,
            require_schema_validation=True,
            system_prompt=build_system_prompt(task_type, allow and need_multi_step),
        )

    def from_rows(self, rows: Iterable[dict]) -> List[EvaluationSignal]:
        result: List[EvaluationSignal] = []
        for row in rows:
            result.append(
                EvaluationSignal(
                    symbol=row['symbol'],
                    market=row['market'],
                    source=row.get('source', 'knot_agent'),
                    dimension=row.get('dimension', 'agent_judgment'),
                    score=float(row.get('score', 0.5)),
                    confidence=float(row.get('confidence', 0.65)),
                    summary=row.get('summary', ''),
                    risks=list(row.get('risks', [])),
                    action_bias=row.get('action_bias', 'neutral'),
                    meta={
                        'json_only': True,
                        'allow_task_planning': False,
                        'schema_validated': bool(row.get('schema_validated', True)),
                        'task_type': row.get('task_type', 'evaluation'),
                    },
                )
            )
        return result
