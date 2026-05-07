from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runs = repo / 'state' / 'runs'
    task = load_json(runs / 'hk_sim_task_report.json') or {}
    futu_buy = load_json(runs / 'futu_sim_submit_demo.json') or {}
    mark = load_json(runs / 'hk_sim_mark_report.json') or {}
    close = load_json(runs / 'hk_sim_close_report.json') or {}
    forced = load_json(runs / 'hk_sim_forced_exit_report.json') or {}
    reconcile = load_json(runs / 'futu_sim_position_reconcile.json') or {}
    remote_hk = load_json(runs / 'knot_agent_raw_output_hk.json') or {}
    out = {
        'summary': {
            'task': task.get('task'),
            'cash': close.get('cash', mark.get('cash')),
            'nav': close.get('nav', mark.get('nav')),
            'risk_status': close.get('risk_status'),
            'realized_pnl': close.get('realized_pnl'),
        },
        'candidate_pool': task.get('task_candidate_pool', []),
        'buy_actions': task.get('actions', []),
        'futu_buy_submit': futu_buy,
        'positions_after_mark': mark.get('positions', []),
        'close_exit_actions': close.get('exit_actions', []),
        'forced_exit_validation': forced.get('exit_actions', []),
        'reconciliation': reconcile.get('diffs', []),
        'remote_ai_summary': remote_hk.get('results', [])[:5],
        'execution_summary': {
            'buy_action_count': len(task.get('actions', [])),
            'close_exit_count': len(close.get('exit_actions', [])),
            'forced_exit_count': len(forced.get('exit_actions', [])),
            'reconcile_diff_count': len(reconcile.get('diffs', [])),
        },
        'latest_orders': close.get('orders', [])[-10:] if close.get('orders') else [],
    }
    path = runs / 'hk_final_brief.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
