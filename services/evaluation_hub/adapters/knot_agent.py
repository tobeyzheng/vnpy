from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from services.evaluation_hub.models import EvaluationSignal


JSON_ONLY_SYSTEM_PROMPT = """
你是一个策略选择器，不是泛化研究助手。
输出格式要求：
- 必须且只能输出一个纯 JSON 对象
- 禁止使用 Markdown 代码块
- 禁止在 JSON 前后添加任何解释性文本
- JSON 必须严格符合指定字段和枚举值
- 对于单次策略选择、规则匹配、JSON 分类任务，禁止使用 task_planning

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
    system_prompt: str = JSON_ONLY_SYSTEM_PROMPT


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
            system_prompt=JSON_ONLY_SYSTEM_PROMPT,
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
