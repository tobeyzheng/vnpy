from __future__ import annotations

from vnpy.alpha.strategy.template import AlphaStrategy
from vnpy.trader.object import BarData, TradeData

from scripts.classic_multifactor.external import ExternalSelectionProvider
from scripts.classic_multifactor.fusion import SignalFusionEngine
from scripts.classic_multifactor.model import ClassicMultiFactorConfig, ClassicMultiFactorModel


class ClassicMultiFactorAlphaStrategy(AlphaStrategy):
    """AlphaStrategy adapter for multi-symbol classic factors plus optional external overlay."""

    fast_window: int = 10
    slow_window: int = 60
    momentum_window: int = 20
    atr_window: int = 14
    entry_score: float = 0.62
    exit_score: float = 0.46
    max_position_pct: float = 0.20
    price_add: float = 0.001

    def on_init(self) -> None:
        self.model = ClassicMultiFactorModel(
            ClassicMultiFactorConfig(
                fast_window=int(self.fast_window),
                slow_window=int(self.slow_window),
                momentum_window=int(self.momentum_window),
                atr_window=int(self.atr_window),
                entry_score=float(self.entry_score),
                exit_score=float(self.exit_score),
                max_position_pct=float(self.max_position_pct),
            )
        )
        self.history: dict[str, list[BarData]] = {vt_symbol: [] for vt_symbol in self.vt_symbols}
        self.fusion = SignalFusionEngine()
        self.external_provider: ExternalSelectionProvider | None = getattr(self, "external_provider", None)

    def on_bars(self, bars: dict[str, BarData]) -> None:
        portfolio_value = max(float(self.get_portfolio_value()), 1.0)
        for vt_symbol, bar in bars.items():
            hist = self.history.setdefault(vt_symbol, [])
            hist.append(bar)
            hist[:] = hist[-(self.model.config.warmup_window + 10):]
            factor = self.model.evaluate_bars(vt_symbol, hist)
            symbol = vt_symbol.replace(".SMART", ".US")
            external = self.external_provider.latest("us", symbol, bar.datetime) if self.external_provider else None
            fused = self.fusion.fuse(factor, external)
            current_pos = int(self.get_pos(vt_symbol))
            if fused.allow_trade and current_pos <= 0 and factor:
                target_value = portfolio_value * min(float(self.max_position_pct), fused.position_multiplier * float(self.max_position_pct))
                target_qty = int(target_value // bar.close_price) if bar.close_price > 0 else 0
                if target_qty > 0:
                    self.set_target(vt_symbol, target_qty)
            elif current_pos > 0 and factor and factor.signal == "exit_or_flat":
                self.set_target(vt_symbol, 0)
        self.execute_trading(bars, self.price_add)

    def on_trade(self, trade: TradeData) -> None:
        pass
