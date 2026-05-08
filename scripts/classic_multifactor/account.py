from __future__ import annotations

from dataclasses import asdict
from typing import Any

from services.futu_account import FutuAccountProvider, FutuQuoteClient
from services.futu_sim_trade import FutuSimTradeClient

from scripts.classic_multifactor.data import parse_us_symbol
from scripts.classic_multifactor.model import ClassicMultiFactorModel
from scripts.classic_multifactor.risk import ClassicOrderRiskManager


class FutuSimAccountManager:
    """Futu SIM account facade for read-only account, position, quote and optional submit."""

    def __init__(self):
        self.account_provider = FutuAccountProvider()
        self.quote_client = FutuQuoteClient()
        self.trade_client = FutuSimTradeClient()

    def snapshot(self, symbol: str) -> dict[str, Any]:
        us_symbol, _vt_symbol, _futu_code = parse_us_symbol(symbol)
        account = self.account_provider.get_summary()
        positions = self.trade_client.get_positions()
        quote_rows = self.quote_client.get_snapshot([us_symbol])
        return {
            "account": account,
            "positions": positions,
            "quote": quote_rows[0] if quote_rows else {},
            "held_qty": self.held_qty(positions, us_symbol),
        }

    def held_qty(self, positions: dict[str, Any], symbol: str) -> int:
        for row in positions.get("items", []):
            if str(row.get("symbol", "")).upper() == symbol.upper():
                return int(row.get("qty") or 0)
        return 0

    def submit(self, *, symbol: str, side: str, qty: int, price: float, reason: str) -> Any:
        return self.trade_client.submit_limit_order(symbol, side, qty, price, reason=reason)


class ClassicSimOnceFlow:
    def __init__(self, *, symbol: str, vt_symbol: str, model: ClassicMultiFactorModel, risk_manager: ClassicOrderRiskManager, submit_sim: bool):
        self.symbol = symbol
        self.vt_symbol = vt_symbol
        self.model = model
        self.risk_manager = risk_manager
        self.submit_sim = submit_sim
        self.account = FutuSimAccountManager()

    def run(self, bars: list) -> dict[str, Any]:
        snapshot = self.account.snapshot(self.symbol)
        factor = self.model.evaluate_bars(self.vt_symbol, bars)
        quote = snapshot["quote"]
        price = float(quote.get("ask_price") or quote.get("last_price") or quote.get("price") or 0.0)
        held_qty = int(snapshot["held_qty"])
        equity = float(snapshot["account"].total_assets or 0.0) or 1.0
        cash = float(snapshot["account"].cash or 0.0)
        side = "HOLD"
        target_qty = held_qty
        if factor and price > 0:
            decision = self.model.decide_target(
                vt_symbol=self.vt_symbol,
                bars=bars,
                current_qty=held_qty,
                entry_price=price,
                highest_close=price,
                equity=equity,
                cash=cash,
                trade_price=price,
            )
            side = decision.side
            target_qty = decision.target_qty
        risk = None
        submit_result = None
        if side in {"BUY", "SELL"}:
            limit_price = round(price * (1.002 if side == "BUY" else 0.998), 4)
            risk = self.risk_manager.size_and_check(
                symbol=self.symbol,
                side=side,
                price=limit_price,
                cash=cash,
                equity=equity,
                current_qty=held_qty,
                target_qty=target_qty,
                factor=factor,
                account_status=str(snapshot["account"].status),
            )
            if self.submit_sim and risk.allowed:
                submit_result = self.account.submit(symbol=self.symbol, side=side, qty=risk.qty, price=limit_price, reason=factor.reason if factor else "classic_multifactor")
        return {
            "submit_sim": self.submit_sim,
            "account": asdict(snapshot["account"]),
            "positions": snapshot["positions"],
            "held_qty": held_qty,
            "quote": quote,
            "latest_factor": asdict(factor) if factor else None,
            "decision": {
                "side": side,
                "target_qty": target_qty,
                "price": price,
                "risk": risk.to_dict() if risk else None,
            },
            "submit_result": asdict(submit_result) if submit_result else None,
        }
