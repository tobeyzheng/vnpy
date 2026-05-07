from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def load(path: Path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runs = repo / 'state' / 'runs'
    hk = load(runs / 'hk_sim_task_report.json')
    us = load(runs / 'us_sim_task_report.json')
    actions = (hk.get('actions', []) or []) + (us.get('actions', []) or [])
    counter = Counter(a.get('action', 'unknown') for a in actions)
    out = {'action_distribution': dict(counter)}
    path = runs / 'action_distribution.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
