from __future__ import annotations

from dataclasses import asdict, dataclass, field

from scripts.classic_multifactor.external import ExternalSelection
from scripts.classic_multifactor.model import FactorSnapshot


@dataclass(frozen=True)
class FusedDecision:
    signal: str
    allow_trade: bool
    position_multiplier: float
    reason: str
    risk_flags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class SignalFusionEngine:
    """Combine deterministic factor signal with optional KnotAgent/external selection.

    External selection can veto/reduce/annotate but cannot turn a flat factor
    signal into a new long entry by itself.
    """

    def fuse(self, factor: FactorSnapshot | None, external: ExternalSelection | None) -> FusedDecision:
        if factor is None:
            return FusedDecision("hold", False, 0.0, "no_factor", metadata={"external": asdict(external) if external else None})
        allow = factor.signal == "long_entry"
        multiplier = 1.0 if allow else 0.0
        reason = factor.reason
        risk_flags: list[str] = []
        if external:
            risk_flags.extend(external.risk_flags)
            if external.strategy_id in {"watch_only", "block_trade"} or not external.allow_trade:
                allow = False
                multiplier = 0.0
                reason = f"external_veto:{external.reason}"
            elif allow:
                multiplier = min(multiplier, external.position_multiplier)
                reason = f"{factor.reason}+external:{external.reason}"
        return FusedDecision(
            signal=factor.signal,
            allow_trade=allow,
            position_multiplier=multiplier,
            reason=reason,
            risk_flags=sorted(set(risk_flags)),
            metadata={"factor": asdict(factor), "external": asdict(external) if external else None},
        )
