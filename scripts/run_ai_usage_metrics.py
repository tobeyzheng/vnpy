from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runs = repo / 'state' / 'runs'
    remote = json.loads((runs / 'knot_agent_raw_output_hk.json').read_text(encoding='utf-8')) if (runs / 'knot_agent_raw_output_hk.json').exists() else {}
    results = remote.get('results', []) if isinstance(remote.get('results', []), list) else []
    runtime_remote = sum(1 for r in results if r.get('runtime') == 'openclaw-subagent-remote' or r.get('remote_result'))
    runtime_fallback = sum(1 for r in results if r.get('runtime') != 'openclaw-subagent-remote' and not r.get('remote_result'))
    out = {
        'ai_usage': {
            'remote_result_count': runtime_remote,
            'fallback_result_count': runtime_fallback,
            'remote_ratio': round(runtime_remote / len(results), 4) if results else 0.0,
        }
    }
    path = runs / 'ai_usage_metrics.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
