from __future__ import annotations

import json
from pathlib import Path

from services.evaluation_hub.adapters.knot_agent_schema import local_strategy_fallback, validate_json_only_response


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    bad = '下面是结果\n```json\n{"strategy":"watch_only","confidence":0.8,"reason":"bad","risk_flags":[]}\n```'
    good = '{"strategy":"pullback_buy","confidence":0.82,"reason":"长期强但短线过热","risk_flags":["overbought"]}'
    fallback_payload = {
        'risk_score': 0.4,
        'capital_score': 0.8,
        'trend_score': 1.0,
        'rsi': 82.1,
        'has_event_catalyst': False,
        'near_resistance': True,
        'moving_average_bullish': True,
        'missing_fields': 0,
    }
    out = {
        'bad_validation': validate_json_only_response(bad).__dict__,
        'good_validation': validate_json_only_response(good).__dict__,
        'fallback': local_strategy_fallback(fallback_payload),
    }
    path = repo / 'state' / 'runs' / 'knot_agent_validation_demo.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
