from __future__ import annotations

import json
from pathlib import Path

from services.evaluation_hub import EvaluationHub
from services.evaluation_hub.adapters import KnotAgentEvaluationAdapter, SkillEvaluationAdapter


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    sample_path = repo / 'examples' / 'evaluation_signals.sample.json'
    rows = json.loads(sample_path.read_text(encoding='utf-8'))
    skill_rows = [r for r in rows if r.get('source') != 'knot_agent']
    agent_rows = [r for r in rows if r.get('source') == 'knot_agent']

    skill_signals = SkillEvaluationAdapter().from_rows(skill_rows)
    agent_signals = KnotAgentEvaluationAdapter().from_rows(agent_rows)
    bundles = EvaluationHub().merge([*skill_signals, *agent_signals])

    out = repo / 'state' / 'runs' / 'evaluation_hub_demo.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([
        {
            'symbol': b.symbol,
            'market': b.market,
            'signal_count': len(b.signals),
            'sources': [s.source for s in b.signals],
            'dimensions': [s.dimension for s in b.signals],
        }
        for b in bundles
    ], ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps([
        {
            'symbol': b.symbol,
            'market': b.market,
            'signal_count': len(b.signals),
            'sources': [s.source for s in b.signals],
            'dimensions': [s.dimension for s in b.signals],
        }
        for b in bundles
    ], ensure_ascii=False))


if __name__ == '__main__':
    main()
