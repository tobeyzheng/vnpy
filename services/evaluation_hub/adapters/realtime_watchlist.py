from __future__ import annotations

from typing import Iterable, List

from services.evaluation_hub.models import EvaluationSignal


class RealtimeWatchlistAdapter:
    def from_quote_items(self, market: str, items: Iterable[dict]) -> List[EvaluationSignal]:
        result: List[EvaluationSignal] = []
        for item in items:
            symbol = item.get('code', '')
            change_pct = float(item.get('change_pct') or 0)
            price = float(item.get('price') or 0)
            volume = float(item.get('volume') or 0)
            turnover = float(item.get('turnover') or 0)
            amplitude = float(item.get('amplitude') or 0)
            bid_price = item.get('bid_price')
            ask_price = item.get('ask_price')

            trend_score = min(1.0, max(0.0, 0.5 + change_pct / 20.0))
            liquidity_score = min(1.0, max(0.0, (turnover / 1e10))) if turnover > 0 else 0.2

            spread_penalty = 0.0
            if bid_price not in (None, 0, 0.0) and ask_price not in (None, 0, 0.0):
                mid = (float(bid_price) + float(ask_price)) / 2
                spread_penalty = min(1.0, max(0.0, abs(float(ask_price) - float(bid_price)) / mid)) if mid else 0.0

            execution_fit = max(0.0, min(1.0, 0.8 - spread_penalty + (0.1 if turnover > 0 else -0.2)))
            risk_penalty = min(1.0, amplitude / 10.0) if amplitude > 0 else 0.0

            result.append(EvaluationSignal(
                symbol=symbol,
                market=market,
                source='realtime_watchlist',
                dimension='market_trend',
                score=trend_score,
                confidence=0.7 if price > 0 else 0.4,
                summary=f"实时涨跌幅 {change_pct:.2f}% / price {price}",
                risks=[] if abs(change_pct) < 8 else ['intraday volatility elevated'],
                action_bias='positive' if change_pct > 0 else 'negative' if change_pct < 0 else 'neutral',
            ))
            result.append(EvaluationSignal(
                symbol=symbol,
                market=market,
                source='realtime_watchlist',
                dimension='liquidity',
                score=liquidity_score,
                confidence=0.75,
                summary=f"turnover={turnover:.0f}, volume={volume:.0f}",
                risks=[] if turnover > 0 else ['thin liquidity'],
                action_bias='neutral',
            ))
            result.append(EvaluationSignal(
                symbol=symbol,
                market=market,
                source='realtime_watchlist',
                dimension='execution_fit',
                score=execution_fit,
                confidence=0.7,
                summary=f"bid={bid_price}, ask={ask_price}",
                risks=[] if execution_fit > 0.4 else ['poor execution fit'],
                action_bias='neutral',
            ))
            result.append(EvaluationSignal(
                symbol=symbol,
                market=market,
                source='realtime_watchlist',
                dimension='risk_penalty',
                score=risk_penalty,
                confidence=0.65,
                summary=f"amplitude={amplitude}",
                risks=['high amplitude'] if amplitude > 5 else [],
                action_bias='negative' if amplitude > 5 else 'neutral',
            ))
        return result
