from __future__ import annotations

import json
from pathlib import Path

from services.futu_sim_trade import FutuSimTradeClient


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    client = FutuSimTradeClient()
    result = client.submit_limit_order('HK.00772', 'BUY', 100, 26.82, reason='内容资产与AI叙事有补涨空间')
    out = repo / 'state' / 'runs' / 'futu_sim_submit_demo.json'
    payload = {'success': result.success, 'message': result.message, 'order_id': result.order_id}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == '__main__':
    main()
