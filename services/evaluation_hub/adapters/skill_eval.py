from __future__ import annotations

from typing import Iterable, List

from services.evaluation_hub.models import EvaluationSignal


class SkillEvaluationAdapter:
    def from_rows(self, rows: Iterable[dict]) -> List[EvaluationSignal]:
        result: List[EvaluationSignal] = []
        for row in rows:
            result.append(
                EvaluationSignal(
                    symbol=row['symbol'],
                    market=row['market'],
                    source=row.get('source', 'stock_skill'),
                    dimension=row.get('dimension', 'skill_consensus'),
                    score=float(row.get('score', 0.5)),
                    confidence=float(row.get('confidence', 0.6)),
                    summary=row.get('summary', ''),
                    risks=list(row.get('risks', [])),
                    action_bias=row.get('action_bias', 'neutral'),
                )
            )
        return result
