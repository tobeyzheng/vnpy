from __future__ import annotations

from typing import Iterable, List

from services.evaluation_hub.models import EvaluationSignal


class KnotAgentEvaluationAdapter:
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
                )
            )
        return result
