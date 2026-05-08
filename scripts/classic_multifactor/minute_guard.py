from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class MinuteTradeGuardConfig:
    max_intraday_trades: int = 4
    entry_cooldown_minutes: int = 30
    min_hold_minutes: int = 20
    no_new_entry_after: str = "15:30"


@dataclass(frozen=True)
class MinuteGuardDecision:
    allowed: bool
    reason: str = ""


class MinuteTradeGuard:
    """Intraday frequency guard for 1m trading.

    The guard is intentionally state-light. Callers own the persisted state so the
    same guard can be reused in vn.py CTA strategy, lightweight backtest and SIM flows.
    """

    def __init__(self, config: MinuteTradeGuardConfig):
        self.config = config

    def can_enter(self, now: datetime, *, trade_times: list[datetime], last_trade_at: datetime | None) -> MinuteGuardDecision:
        today_count = self._today_count(now, trade_times)
        if self.config.max_intraday_trades > 0 and today_count >= self.config.max_intraday_trades:
            return MinuteGuardDecision(False, "max_intraday_trades_reached")
        cutoff = self._cutoff_time()
        if cutoff and now.time() >= cutoff:
            return MinuteGuardDecision(False, "no_new_entry_after_cutoff")
        if last_trade_at and self.config.entry_cooldown_minutes > 0:
            if now - last_trade_at < timedelta(minutes=self.config.entry_cooldown_minutes):
                return MinuteGuardDecision(False, "entry_cooldown")
        return MinuteGuardDecision(True, "ok")

    def can_exit(self, now: datetime, *, entry_at: datetime | None, hard_exit: bool = False) -> MinuteGuardDecision:
        if hard_exit:
            return MinuteGuardDecision(True, "hard_exit")
        if entry_at and self.config.min_hold_minutes > 0:
            if now - entry_at < timedelta(minutes=self.config.min_hold_minutes):
                return MinuteGuardDecision(False, "min_hold_minutes")
        return MinuteGuardDecision(True, "ok")

    def _today_count(self, now: datetime, trade_times: list[datetime]) -> int:
        return sum(1 for value in trade_times if value.date() == now.date())

    def _cutoff_time(self) -> time | None:
        text = str(self.config.no_new_entry_after or "").strip()
        if not text:
            return None
        hour, minute = text.split(":", 1)
        return time(int(hour), int(minute))
