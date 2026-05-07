from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 5:
        raise SystemExit('usage: write_remote_knot_result.py <target> <symbol> <task_type> <json_result>')
    repo = Path(__file__).resolve().parents[1]
    target_name, symbol, task_type, json_result = sys.argv[1:5]
    path = repo / 'state' / 'runs' / target_name
    data = {'results': []}
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        if 'results' not in data or not isinstance(data['results'], list):
            data['results'] = []
    parsed = json.loads(json_result)
    replaced = False
    for i, item in enumerate(data['results']):
        if item.get('symbol') == symbol and item.get('task_type') == task_type:
            data['results'][i]['remote_result'] = parsed
            data['results'][i]['runtime'] = 'openclaw-subagent-remote'
            replaced = True
            break
    if not replaced:
        data['results'].append({'symbol': symbol, 'task_type': task_type, 'remote_result': parsed, 'runtime': 'openclaw-subagent-remote'})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps({'symbol': symbol, 'task_type': task_type, 'written': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
