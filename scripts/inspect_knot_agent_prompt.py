from __future__ import annotations

import json
from pathlib import Path

from services.evaluation_hub.adapters.knot_agent import KnotAgentEvaluationAdapter


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    adapter = KnotAgentEvaluationAdapter()
    payload = {
        'strategy_select': adapter.prompt_spec(task_type='strategy_select', fields_present=8, need_multi_step=False).__dict__,
        'evaluation': adapter.prompt_spec(task_type='evaluation', fields_present=3, need_multi_step=True).__dict__,
    }
    out = repo / 'state' / 'runs' / 'knot_agent_prompt_spec.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
