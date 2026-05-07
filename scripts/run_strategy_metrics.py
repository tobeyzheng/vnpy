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
    remote = load(runs / 'knot_agent_raw_output_hk.json')

    all_actions = (hk_task.get('actions', []) or []) + (us_task.get('actions', []) or [])
    action_count = len(all_actions)
    filled_count = sum(1 for a in all_actions if a.get('action') == 'filled')
    watch_count = sum(1 for a in all_actions if a.get('action') == 'watch_only')
    blocked_count = sum(1 for a in all_actions if a.get('action') == 'blocked')

    metrics = {
        'metrics': {
            'action_count': action_count,
            'filled_count': filled_count,
            'watch_only_count': watch_count,
            'blocked_count': blocked_count,
            'fill_rate': round(filled_count / action_count, 4) if action_count else 0.0,
            'watch_rate': round(watch_count / action_count, 4) if action_count else 0.0,
            'blocked_rate': round(blocked_count / action_count, 4) if action_count else 0.0,
            'portfolio_realized_pnl': round(float(hk_close.get('realized_pnl', 0) or 0) + float(us_close.get('realized_pnl', 0) or 0), 4),
            'remote_ai_item_count': len(remote.get('results', [])) if isinstance(remote.get('results', []), list) else 0,
        }
    }
    path = runs / 'strategy_metrics.json'
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
