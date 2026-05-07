from __future__ import annotations

import json
from pathlib import Path

from services.healthcheck import HealthcheckService


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    report = HealthcheckService(repo).run()
    path = repo / 'state' / 'runs' / 'healthcheck.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(path)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
