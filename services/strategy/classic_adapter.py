from __future__ import annotations

"""Classic multifactor adapter that plugs ``ClassicMultiFactorModel`` into the
live pipeline as a first-class evaluator.

Rationale (see ``.codebuddy/plan/classic_multifactor_audit/requirements.md`` B1):
the live path previously consumed ``services/strategy/engine.py``'s generic
score + timing + selector chain with a hard-coded ``raw_score=0.8`` on the
candidate. That has *no* mapping back to the backtest decision function
``ClassicMultiFactorModel.decide_target``. This adapter closes that gap so
"backtest decision == live decision" for classic_multifactor candidates.

The adapter is intentionally **additive**: it exposes a
``ClassicSignalAdapter.evaluate`` helper that returns a ``StrategyEvaluation``
compatible with the existing live pipeline, plus a structured
``ClassicDecision`` payload that includes the exit reason path (stop loss,
trailing, take-profit, ATR).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from services.common import StrategySignal
from services.strategy.engine import StrategyEvaluation
from services.strategy.raw_score import RawScoreFeatures
from services.strategy.strategy_selector import StrategySelection
from services.strategy.timing import TimingDecision

from scripts.classic_multifactor.config_schema import model_config_from_setting
from scripts.classic_multifactor.model import (
    ClassicMultiFactorConfig,
    ClassicMultiFactorModel,
    TargetDecision,
)


# Set of reasons that constitute a hard exit (bypass min_hold_minutes guard).
HARD_EXIT_REASONS = frozenset({
    "stop_loss",
    "atr_stop_loss",
    "trailing_stop",
    "atr_trailing_stop",
    "take_profit",
    "atr_take_profit",
})

# Reasons that specifically mean "model wants to wait another bar" — distinct
# from a risk-layer block. Exposed so callers can fail-close with explicit
# context in the live report.
WAITING_REASONS = frozenset({
    "warming_up",
    "entry_not_confirmed",
    "classic_multifactor_hold",
})


@dataclass
class ClassicDecision:
    """Typed wrapper around ``TargetDecision`` with live-friendly fields."""

    side: str  # "BUY" / "SELL" / "HOLD"
    allow_trade: bool
    target_qty: int
    raw_score: float
    confidence: float
    reason: str
    hard_exit: bool = False
    exit_reason: str | None = None  # Only set when side == "SELL".
    factor: dict[str, Any] = field(default_factory=dict)
    warming_up: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "allow_trade": self.allow_trade,
            "target_qty": self.target_qty,
            "raw_score": round(float(self.raw_score), 4),
            "confidence": round(float(self.confidence), 4),
            "reason": self.reason,
            "hard_exit": self.hard_exit,
            "exit_reason": self.exit_reason,
            "warming_up": self.warming_up,
            "factor": self.factor,
        }


class ClassicSignalAdapter:
    """Adapter that drives ``ClassicMultiFactorModel`` from the live pipeline.

    ``bar_loader`` is an injectable callable ``(vt_symbol, start, end) -> list[BarData]``
    so the adapter can be unit-tested without a live database. In production
    the live pipeline passes a thin wrapper around ``VnpyBarRepository``.
    """

    def __init__(
        self,
        *,
        bar_loader: Callable[[str, datetime, datetime], list[Any]] | None = None,
        default_strategy_id: str = "classic_multifactor_cta",
    ):
        self.bar_loader = bar_loader
        self.default_strategy_id = default_strategy_id

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------
    def evaluate(
        self,
        *,
        candidate: Mapping[str, Any],
        quote: Mapping[str, Any],
        current_qty: int = 0,
        entry_price: float = 0.0,
        highest_close: float = 0.0,
        equity: float = 0.0,
        cash: float = 0.0,
        now: datetime | None = None,
        bars: list[Any] | None = None,
    ) -> ClassicDecision:
        """Run the model once and return a typed ``ClassicDecision``.

        ``bars`` lets callers provide pre-loaded data (e.g. cached per loop
        iteration). Otherwise the bound ``bar_loader`` is used.
        """
        setting = self._extract_setting(candidate)
        cfg = model_config_from_setting(setting)
        model = ClassicMultiFactorModel(cfg)

        vt_symbol = self._to_vt_symbol(candidate)
        if bars is None:
            bars = self._load_bars(vt_symbol, cfg, now=now)

        if not bars or len(bars) < cfg.warmup_window + 1:
            return ClassicDecision(
                side="HOLD",
                allow_trade=False,
                target_qty=0,
                raw_score=0.0,
                confidence=0.0,
                reason="warmup_insufficient",
                factor={},
                warming_up=True,
            )

        trade_price = self._trade_price(quote)
        decision: TargetDecision = model.decide_target(
            vt_symbol=vt_symbol,
            bars=bars,
            current_qty=int(current_qty or 0),
            entry_price=float(entry_price or 0.0),
            highest_close=float(highest_close or 0.0),
            equity=float(equity or 0.0),
            cash=float(cash or 0.0),
            trade_price=float(trade_price or 0.0),
        )
        return self._decision_to_classic(decision, quote=quote)

    # ------------------------------------------------------------------
    # Convert to StrategyEvaluation so the live pipeline can consume it.
    # ------------------------------------------------------------------
    def to_evaluation(
        self,
        *,
        candidate: Mapping[str, Any],
        classic: ClassicDecision,
    ) -> StrategyEvaluation:
        strategy_id = self._strategy_id(candidate)
        allow_trade = bool(classic.allow_trade and classic.side == "BUY")
        direction = "long" if allow_trade else "flat"
        features = RawScoreFeatures(
            trend_score=float(classic.factor.get("trend_score", classic.raw_score) or classic.raw_score),
            momentum_score=float(classic.factor.get("momentum_score", 0.5) or 0.5),
            flow_score=float(classic.factor.get("volume_score", 0.5) or 0.5),
            quality_score=float(classic.factor.get("low_risk_score", 0.5) or 0.5),
            event_score=0.5,
            risk_penalty=1.0 - float(classic.factor.get("low_risk_score", 0.5) or 0.5),
            legacy_score=float(classic.raw_score),
        )
        timing_action = strategy_id if allow_trade else ("exit_signal" if classic.side == "SELL" else "watch_only")
        entry_timing = TimingDecision(
            action=timing_action,
            reason=classic.reason,
            confidence=float(classic.confidence),
            invalidator=classic.exit_reason or "",
            suggested_size_pct=min(1.0, max(0.0, float(classic.raw_score))) if allow_trade else 0.0,
        )
        selection = StrategySelection(
            strategy_id=strategy_id if allow_trade else "watch_only",
            allow_trade=allow_trade,
            confidence=float(classic.confidence),
            reason=classic.reason,
            source="classic_multifactor_model",
            risk_flags=[] if allow_trade else [classic.reason] if classic.reason else [],
            metadata={
                "classic_decision": classic.to_dict(),
                "warming_up": classic.warming_up,
            },
        )
        signal = StrategySignal(
            strategy_id=strategy_id,
            symbol=str(candidate.get("symbol", "")),
            market=str(candidate.get("market", "")),
            direction=direction,
            score=float(classic.raw_score),
            confidence=float(classic.confidence),
            allow_trade=allow_trade,
            target_position_pct=entry_timing.suggested_size_pct,
            reason=classic.reason,
            risk_flags=selection.risk_flags,
            metadata={
                "entry_action": entry_timing.action,
                "invalidator": entry_timing.invalidator,
                "strategy_selection": selection.to_dict(),
                "classic_decision": classic.to_dict(),
                "exit_reason": classic.exit_reason,
                "hard_exit": classic.hard_exit,
                "source": "classic_multifactor_model",
            },
        )
        return StrategyEvaluation(
            signal=signal,
            raw_score=float(classic.raw_score),
            entry_timing=entry_timing,
            features=features,
            task_score=float(classic.raw_score) * 100,
            metadata={
                "strategy_selection": selection.to_dict(),
                "classic_decision": classic.to_dict(),
                "exit_reason": classic.exit_reason,
                "hard_exit": classic.hard_exit,
                "source": "classic_multifactor_model",
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def is_classic_candidate(candidate: Mapping[str, Any]) -> bool:
        cfg = candidate.get("strategy_config")
        if not isinstance(cfg, dict):
            return False
        # A candidate is considered classic_multifactor if it carries any of
        # the model-specific knobs. This is strict enough to avoid hijacking
        # non-classic candidates but lenient enough to cover partial configs.
        markers = {"entry_score", "exit_score", "fast_window", "slow_window", "atr_window"}
        return any(key in cfg for key in markers)

    @staticmethod
    def _extract_setting(candidate: Mapping[str, Any]) -> dict[str, Any]:
        cfg = candidate.get("strategy_config") or {}
        return dict(cfg) if isinstance(cfg, dict) else {}

    @staticmethod
    def _to_vt_symbol(candidate: Mapping[str, Any]) -> str:
        symbol = str(candidate.get("symbol", "")).strip()
        if not symbol:
            return ""
        if symbol.endswith(".US"):
            code = symbol.rsplit(".", 1)[0]
            return f"{code}.SMART"
        return symbol

    @staticmethod
    def _trade_price(quote: Mapping[str, Any]) -> float:
        for key in ("price", "last_price", "close", "close_price"):
            v = quote.get(key)
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                return fv
        return 0.0

    def _strategy_id(self, candidate: Mapping[str, Any]) -> str:
        sel = candidate.get("strategy_selection")
        if isinstance(sel, dict):
            sid = sel.get("strategy_id")
            if isinstance(sid, str) and sid:
                return sid
        return self.default_strategy_id

    def _load_bars(self, vt_symbol: str, cfg: ClassicMultiFactorConfig, *, now: datetime | None) -> list[Any]:
        if not vt_symbol or self.bar_loader is None:
            return []
        end = now or datetime.now()
        # Pull ~4x the warmup so confirm_bars / ATR have stable history.
        minutes = max(cfg.warmup_window * max(int(cfg.signal_interval_minutes), 1) * 4, 120)
        # For 1m bars we ask for ~4 trading days of calendar time (weekends +
        # overnights); the DB layer will simply return what it has.
        start = end - timedelta(minutes=minutes)
        try:
            bars = self.bar_loader(vt_symbol, start, end)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[classic_adapter] bar_loader failed for {vt_symbol}: {exc}", flush=True)
            return []
        return bars or []

    def _decision_to_classic(self, decision: TargetDecision, *, quote: Mapping[str, Any]) -> ClassicDecision:
        factor = decision.factor
        factor_payload: dict[str, Any] = {}
        raw_score = 0.0
        if factor is not None:
            raw_score = float(getattr(factor, "raw_score", 0.0) or 0.0)
            factor_payload = {
                "raw_score": raw_score,
                "trend_score": float(getattr(factor, "trend_score", 0.0) or 0.0),
                "momentum_score": float(getattr(factor, "momentum_score", 0.0) or 0.0),
                "volume_score": float(getattr(factor, "volume_score", 0.0) or 0.0),
                "low_risk_score": float(getattr(factor, "low_risk_score", 0.0) or 0.0),
                "breakout_score": float(getattr(factor, "breakout_score", 0.0) or 0.0),
                "atr_pct": float(getattr(factor, "atr_pct", 0.0) or 0.0),
                "atr_value": float(getattr(factor, "atr_value", 0.0) or 0.0),
                "close": float(getattr(factor, "close", 0.0) or 0.0),
                "signal": getattr(factor, "signal", ""),
                "reason": getattr(factor, "reason", ""),
                "datetime": getattr(factor, "datetime", ""),
            }

        side = decision.side or "HOLD"
        reason = decision.reason or ""
        is_waiting = reason in WAITING_REASONS or side == "HOLD"

        if side == "BUY":
            return ClassicDecision(
                side="BUY",
                allow_trade=decision.target_qty > 0,
                target_qty=int(decision.target_qty or 0),
                raw_score=raw_score,
                confidence=0.82 if decision.target_qty > 0 else 0.6,
                reason=reason or "classic_multifactor_entry",
                hard_exit=False,
                exit_reason=None,
                factor=factor_payload,
                warming_up=False,
            )
        if side == "SELL":
            return ClassicDecision(
                side="SELL",
                allow_trade=True,
                target_qty=0,
                raw_score=raw_score,
                confidence=0.95 if reason in HARD_EXIT_REASONS else 0.78,
                reason=reason or "classic_multifactor_exit",
                hard_exit=reason in HARD_EXIT_REASONS,
                exit_reason=reason,
                factor=factor_payload,
                warming_up=False,
            )
        return ClassicDecision(
            side="HOLD",
            allow_trade=False,
            target_qty=int(decision.target_qty or 0),
            raw_score=raw_score,
            confidence=0.5,
            reason=reason or "hold",
            hard_exit=False,
            exit_reason=None,
            factor=factor_payload,
            warming_up=is_waiting,
        )


__all__ = [
    "ClassicDecision",
    "ClassicSignalAdapter",
    "HARD_EXIT_REASONS",
    "WAITING_REASONS",
]
