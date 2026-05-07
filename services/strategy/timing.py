from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimingDecision:
    action: str
    reason: str
    confidence: float


class EntryTimingEngine:
    def decide(self, *, trend_score: float, rsi: float, has_event_catalyst: bool, near_resistance: bool, moving_average_bullish: bool) -> TimingDecision:
        if near_resistance and rsi >= 75 and not has_event_catalyst:
            return TimingDecision('watch_only', '临近阻力且偏热，等待确认', 0.78)
        if has_event_catalyst and near_resistance:
            return TimingDecision('breakout_momentum', '接近关键位且存在催化，适合等待突破确认', 0.8)
        if moving_average_bullish and trend_score >= 0.68 and rsi < 70:
            return TimingDecision('trend_following', '趋势和均线结构健康，可顺势参与', 0.82)
        if trend_score >= 0.75 and 45 <= rsi <= 60:
            return TimingDecision('pullback_buy', '趋势良好且位置相对中性，适合回踩布局', 0.76)
        return TimingDecision('watch_only', '入场条件不足，先观察', 0.68)


class ExitTimingEngine:
    def decide(self, *, pnl_pct: float, rsi: float, trend_score: float, risk_score: float) -> TimingDecision:
        if pnl_pct <= -0.08:
            return TimingDecision('stop_loss', '触发固定止损', 0.95)
        if pnl_pct >= 0.15:
            return TimingDecision('take_profit', '触发固定止盈', 0.92)
        if rsi >= 82 and trend_score < 0.7:
            return TimingDecision('trim_or_exit', '高位过热且趋势边际走弱', 0.78)
        if risk_score >= 0.75:
            return TimingDecision('reduce_risk', '风险评分升高，优先降风险', 0.74)
        return TimingDecision('hold', '继续持有观察', 0.66)
