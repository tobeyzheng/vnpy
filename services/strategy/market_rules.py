from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarketRules:
    market: str
    currency: str
    lot_size_mode: str
    default_stop_loss_pct: float
    default_take_profit_pct: float
    slippage_bps: float


def get_market_rules(market: str) -> MarketRules:
    if market == 'hong_kong':
        return MarketRules(market='hong_kong', currency='HKD', lot_size_mode='dynamic', default_stop_loss_pct=0.08, default_take_profit_pct=0.15, slippage_bps=8)
    if market == 'us':
        return MarketRules(market='us', currency='USD', lot_size_mode='unitary', default_stop_loss_pct=0.07, default_take_profit_pct=0.18, slippage_bps=5)
    return MarketRules(market=market, currency='USD', lot_size_mode='unitary', default_stop_loss_pct=0.08, default_take_profit_pct=0.15, slippage_bps=8)
