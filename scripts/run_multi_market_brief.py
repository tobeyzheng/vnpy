from __future__ import annotations

import json
from pathlib import Path


def load(path: Path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runs = repo / 'state' / 'runs'
    hk_task = load(runs / 'hk_sim_task_report.json')
    us_task = load(runs / 'us_sim_task_report.json')
    hk_close = load(runs / 'hk_sim_close_report.json')
    us_close = load(runs / 'us_sim_close_report.json')
    out = {
        'summary': {
            'hk_candidates': len(hk_task.get('task_candidate_pool', [])),
            'us_candidates': len(us_task.get('task_candidate_pool', [])),
            'hk_actions': hk_task.get('actions', []),
            'us_actions': us_task.get('actions', []),
            'hk_risk_status': hk_close.get('risk_status'),
            'us_risk_status': us_close.get('risk_status'),
        }
    }
    path = runs / 'multi_market_brief.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
