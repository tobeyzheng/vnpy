from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runs = repo / 'state' / 'runs'
    source = runs / 'candidate_inputs.json'
    target = runs / 'candidate_inputs.dynamic.json'

    # 当前阶段先输出动态候选协议文件骨架：
    # 真实 knot agent 批量生成由 OpenClaw 编排层接入。
    rows = json.loads(source.read_text(encoding='utf-8')) if source.exists() else []
    out = {
        'mode': 'dynamic_candidate_generation_scaffold',
        'next_step': 'populate candidates via knot agent batch orchestration',
        'markets': sorted(list({row.get('market') for row in rows if row.get('market')})),
        'count_seed_rows': len(rows),
        'items': rows,
    }
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(target)
    print(json.dumps({'mode': out['mode'], 'markets': out['markets'], 'count_seed_rows': out['count_seed_rows']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
