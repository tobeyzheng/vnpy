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
    remote = load(runs / 'knot_agent_raw_output_hk.json')
    hk_task = load(runs / 'hk_sim_task_report.json')
    us_task = load(runs / 'us_sim_task_report.json')
    remote_results = remote.get('results', []) if isinstance(remote.get('results', []), list) else []
    task_symbols = [x.get('symbol') for x in (hk_task.get('task_candidate_pool', []) + us_task.get('task_candidate_pool', []))]
    covered = set()
    for item in remote_results:
        sym = item.get('symbol')
        if sym in task_symbols and (item.get('remote_result') or item.get('runtime') == 'openclaw-subagent-remote'):
            covered.add(sym)
    out = {
        'coverage': {
            'task_symbol_count': len(task_symbols),
            'remote_covered_symbol_count': len(covered),
            'remote_coverage_ratio': round(len(covered) / len(task_symbols), 4) if task_symbols else 0.0,
            'covered_symbols': sorted(covered),
        }
    }
    path = runs / 'coverage_metrics.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
