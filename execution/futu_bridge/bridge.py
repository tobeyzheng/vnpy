from __future__ import annotations

from typing import List

from execution.paper_bridge import PaperTradeIntent
from services.futu_opend import OpenDConfig

from .models import FutuOrderDraft


class FutuPaperBridge:
    def __init__(self, config: OpenDConfig | None = None):
        self.config = config or OpenDConfig()

    def _to_futu_code(self, symbol: str, market: str) -> str:
        if market == "us":
            ticker = symbol.split(".")[0]
            return f"US.{ticker}"
        if market == "hong_kong":
            ticker = symbol.split(".")[0]
            return f"HK.{ticker}"
        ticker, exchange = symbol.split(".")
        if exchange == "SH":
            return f"SH.{ticker}"
        return f"SZ.{ticker}"

    def build_drafts(self, intents: List[PaperTradeIntent]) -> List[FutuOrderDraft]:
        drafts: List[FutuOrderDraft] = []
        for intent in intents:
            drafts.append(
                FutuOrderDraft(
                    code=self._to_futu_code(intent.symbol, intent.market),
                    trd_env=self.config.trd_env,
                    side=intent.side,
                    reason=intent.reason,
                    target_position_pct=intent.target_position_pct,
                    confidence=intent.confidence,
                    notes=["futu_draft_only", "not_submitted"],
                )
            )
        return drafts
