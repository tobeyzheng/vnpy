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
    hk = load(runs / 'hk_sim_close_report.json')
    us = load(runs / 'us_sim_close_report.json')
    remote = load(runs / 'knot_agent_raw_output_hk.json')
    review = {
        'review_summary': {
            'hk_nav': hk.get('nav'),
            'us_nav': us.get('nav'),
            'hk_realized_pnl': hk.get('realized_pnl'),
            'us_realized_pnl': us.get('realized_pnl'),
            'remote_ai_items': len(remote.get('results', [])) if isinstance(remote.get('results', []), list) else 0,
        },
        'observations': [
            '港股当前候选多为 watch_only，说明 timing 过滤已开始起作用。',
            '美股已出现可执行买点（NVDA），说明多市场框架已初步分化。',
            '远程 AI 结果已进入主流程文件，但仍需进一步批量替换 fallback 结果。',
            'Futu 持仓对账仍是执行一致性的重点风险项。',
        ]
    }
    path = runs / 'strategy_review.json'
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(review, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
