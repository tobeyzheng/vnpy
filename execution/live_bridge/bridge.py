from __future__ import annotations

from execution.paper_bridge.models import PaperTradeIntent

from .models import LiveOrderRequest


class LiveOrderBridge:
    def build_requests(self, intents: list[PaperTradeIntent]) -> list[LiveOrderRequest]:
        requests: list[LiveOrderRequest] = []
        for intent in intents:
            requests.append(
                LiveOrderRequest(
                    symbol=intent.symbol,
                    market=intent.market,
                    side=(intent.side or 'BUY').upper(),
                    qty=float(intent.target_position_pct or 0.0),
                    order_type='LIMIT',
                    price=None,
                    reason=intent.reason,
                    tags=['draft', 'live-prep', *intent.tags],
                )
            )
        return requests
