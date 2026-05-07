from __future__ import annotations

from typing import List

from execution.paper_bridge import PaperTradeIntent

from .models import VnpyOrderDraft


class VnpySignalBridge:
    """Convert paper-trade intents into vnpy order request drafts.

    This bridge is draft-only and must not send live orders.
    """

    def build_drafts(self, intents: List[PaperTradeIntent]) -> List[VnpyOrderDraft]:
        drafts: List[VnpyOrderDraft] = []
        for intent in intents:
            drafts.append(
                VnpyOrderDraft(
                    symbol=intent.symbol,
                    market=intent.market,
                    direction=intent.side,
                    reason=intent.reason,
                    confidence=intent.confidence,
                    target_position_pct=intent.target_position_pct,
                    notes=["draft_only", "not_submitted"],
                )
            )
        return drafts
