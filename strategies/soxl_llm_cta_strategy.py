from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

try:
    from soxl_cta_strategy import NY_TZ, SoxlCtaStrategy
except ImportError:
    from .soxl_cta_strategy import NY_TZ, SoxlCtaStrategy

from vnpy_llm.risk import RiskPolicy, make_risk_decision
from vnpy_llm.signal_store import SignalStore


class SoxlLlmCtaStrategy(SoxlCtaStrategy):
    """SOXL CTA strategy with cached LLM risk filter."""

    author = "CodeBuddy"

    llm_signal_dir: str = "examples/futu_trader/output/llm_signals"
    llm_symbol: str = "SOXL.SMART"
    llm_min_confidence: float = 0.55
    llm_stale_after_hours: int = 30
    llm_high_risk_threshold: float = 0.75
    llm_block_risk_threshold: float = 0.85
    llm_fail_closed: bool = True

    llm_signal_id: str = ""
    llm_decision_reason: str = ""
    llm_position_multiplier: float = 0.0
    llm_allow_new_long: bool = False

    parameters = [
        *SoxlCtaStrategy.parameters,
        "llm_signal_dir",
        "llm_symbol",
        "llm_min_confidence",
        "llm_stale_after_hours",
        "llm_high_risk_threshold",
        "llm_block_risk_threshold",
        "llm_fail_closed",
    ]

    variables = [
        *SoxlCtaStrategy.variables,
        "llm_signal_id",
        "llm_decision_reason",
        "llm_position_multiplier",
        "llm_allow_new_long",
    ]

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.llm_store: SignalStore | None = None

    def on_init(self) -> None:
        self.llm_store = SignalStore(Path(self.llm_signal_dir))
        super().on_init()

    def calculate_target_pos(self, price: float) -> int:
        base_volume = super().calculate_target_pos(price)
        if base_volume <= 0:
            self.llm_allow_new_long = False
            self.llm_position_multiplier = 0.0
            self.llm_decision_reason = "no_cta_entry_signal"
            return 0

        policy = RiskPolicy(
            min_confidence=self.llm_min_confidence,
            stale_after_hours=self.llm_stale_after_hours,
            high_risk_threshold=self.llm_high_risk_threshold,
            block_risk_threshold=self.llm_block_risk_threshold,
            fail_closed=self.llm_fail_closed,
        )
        store = self.llm_store or SignalStore(Path(self.llm_signal_dir))
        signal = store.latest(self.llm_symbol, datetime.now(NY_TZ))
        decision = make_risk_decision(signal, policy, datetime.now(NY_TZ))

        self.llm_signal_id = decision.signal_id or ""
        self.llm_decision_reason = decision.reason
        self.llm_position_multiplier = decision.position_multiplier
        self.llm_allow_new_long = decision.allow_new_long

        if not decision.allow_new_long:
            self.last_signal = f"llm_block:{decision.reason}"
            return 0

        adjusted = math.floor(base_volume * decision.position_multiplier)
        return self.round_volume(adjusted)
