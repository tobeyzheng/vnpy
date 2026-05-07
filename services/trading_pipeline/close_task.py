from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from services.futu_account import FutuAccountProvider
from services.sim_account import SimAccountStore, SimTradingEngine
from services.strategy.timing import ExitTimingEngine
from services.trade_state import OrderStateStore


@dataclass(frozen=True)
class MarketCloseTaskConfig:
    market: str
    task_name: str
    account_filename: str
    report_filename: str
    lot_size_default: int
    quote_prefix: str
    symbol_suffix: str


class SimCloseTradingPipeline:
    def __init__(self, repo_root: Path, config: MarketCloseTaskConfig):
        self.repo_root = repo_root
        self.config = config
        self.account_store = SimAccountStore(repo_root / "state" / "runs" / config.account_filename)
        self.report_path = repo_root / "state" / "runs" / config.report_filename
        self.order_state_store = OrderStateStore(repo_root / "state" / "runs" / "orders")
        self.engine = SimTradingEngine(lot_size_default=config.lot_size_default, order_state_store=self.order_state_store)
        self.exit_engine = ExitTimingEngine()
        self.account_provider = FutuAccountProvider()

    def run(self) -> dict[str, Any]:
        account = self.account_store.load()
        codes = [p.symbol for p in account.positions]
        snapshot = self.account_provider.get_watchlist_snapshot(codes) if codes else {"items": [], "status": "connected", "message": "no positions"}
        quote_map = self._quote_map(snapshot.get("items", []))
        exit_actions = []
        for pos in list(account.positions):
            price = quote_map.get(pos.symbol, pos.avg_price)
            pnl_pct = (price - pos.avg_price) / pos.avg_price if pos.avg_price else 0.0
            timing = self.exit_engine.decide(
                pnl_pct=pnl_pct,
                rsi=70.0,
                trend_score=0.6 if pnl_pct < 0 else 0.72,
                risk_score=0.4 if abs(pnl_pct) < 0.05 else 0.78,
            )
            reason = timing.action if timing.action in {"stop_loss", "take_profit", "trim_or_exit", "reduce_risk"} else None
            if reason:
                order = self.engine.place_sell(account, pos.symbol, price, reason)
                exit_actions.append(asdict(order))
        self.engine.mark_to_market(account, quote_map)
        self.account_store.save(account)
        drawdown = max(0.0, (account.initial_cash - account.nav) / account.initial_cash) if account.initial_cash > 0 else 0.0
        report = {
            "task": self.config.task_name,
            "market": self.config.market,
            "cash": account.cash,
            "nav": account.nav,
            "realized_pnl": account.realized_pnl,
            "drawdown_pct": drawdown,
            "drawdown_limit_pct": account.max_drawdown_limit_pct,
            "risk_status": "stop_new_trades" if drawdown >= account.max_drawdown_limit_pct else "normal",
            "positions": [asdict(p) for p in account.positions],
            "orders": [asdict(o) for o in account.orders[-20:]],
            "exit_actions": exit_actions,
            "snapshot_status": snapshot.get("status"),
            "snapshot_message": snapshot.get("message"),
        }
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def _quote_map(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        result: dict[str, float] = {}
        for item in rows:
            if item.get("price") is None:
                continue
            symbol = self._futu_code_to_symbol(str(item.get("code", "")))
            result[symbol] = float(item["price"])
        return result

    def _futu_code_to_symbol(self, code: str) -> str:
        prefix = f"{self.config.quote_prefix}."
        suffix = f".{self.config.symbol_suffix}"
        if code.startswith(prefix):
            return f"{code.replace(prefix, '', 1)}{suffix}"
        if code.endswith(suffix):
            return code
        return code
