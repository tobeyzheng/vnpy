from __future__ import annotations

import hashlib

from execution.paper_bridge.models import PaperTradeIntent

from .models import LiveOrderRequest


class LiveOrderBridge:
    def build_requests(self, intents: list[PaperTradeIntent]) -> list[LiveOrderRequest]:
        requests: list[LiveOrderRequest] = []
        for intent in intents:
            side = (intent.side or '').upper()
            if side not in {'BUY', 'SELL'}:
                continue
            qty = float(intent.target_position_pct or 0.0)
            request_id = hashlib.md5(f"{intent.symbol}|{intent.market}|{side}|{qty}|{intent.reason}".encode('utf-8')).hexdigest()[:12]
            requests.append(
                LiveOrderRequest(
                    request_id=request_id,
                    symbol=intent.symbol,
                    market=intent.market,
                    side=side,
                    qty=qty,
                    order_type='LIMIT',
                    price=None,
                    reason=intent.reason,
                    source='paper_intent',
                    mode='live_prep',
                    tags=['draft', 'live-prep', *intent.tags],
                )
            )
        return requests
