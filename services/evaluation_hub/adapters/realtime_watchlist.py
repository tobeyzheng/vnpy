from __future__ import annotations

from typing import Iterable, List

from services.evaluation_hub.models import EvaluationSignal


class RealtimeWatchlistAdapter:
    def from_quote_items(self, market: str, items: Iterable[dict]) -> List[EvaluationSignal]:
        result: List[EvaluationSignal] = []
        for item in items:
            change_pct = float(item.get('change_pct') or 0)
            price = float(item.get('price') or 0)
            score = min(1.0, max(0.0, 0.5 + change_pct / 20.0))
            confidence = 0.7 if price > 0 else 0.4
            result.append(
                EvaluationSignal(
                    symbol=item.get('code', ''),
                    market=market,
                    source='realtime_watchlist',
                    dimension='market_trend',
                    score=score,
                    confidence=confidence,
                    summary=f"实时涨跌幅 {change_pct:.2f}% / price {price}",
                    risks=[] if abs(change_pct) < 8 else ['intraday volatility elevated'],
                    action_bias='positive' if change_pct > 0 else 'negative' if change_pct < 0 else 'neutral',
                )
            )
            result.append(
                EvaluationSignal(
                    symbol=item.get('code', ''),
                    market=market,
                    source='realtime_watchlist',
                    dimension='execution_fit',
                    score=0.75 if price > 0 else 0.2,
                    confidence=0.6,
                    summary='存在可读取报价，具备基础执行可行性' if price > 0 else '缺少有效报价',
                    risks=[] if price > 0 else ['missing quote'],
                    action_bias='neutral',
                )
            )
        return result
