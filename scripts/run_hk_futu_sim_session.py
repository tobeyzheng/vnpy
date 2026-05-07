from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.common import OrderIntent
from services.futu_account import FutuAccountProvider, FutuQuoteClient
from services.futu_sim_trade import FutuSimTradeClient
from services.strategy.candidate_provider import UnifiedCandidateProvider
from services.strategy.engine import StrategyEngine
from services.trade_state import OrderStateStore
from services.trade_state.state_machine import OrderStateMachine

FALLBACK_SYMBOLS = ["0700.HK", "9988.HK", "1810.HK", "3690.HK", "0388.HK", "1299.HK", "1024.HK", "9618.HK"]
OPEN_ORDER_STATUSES = {"SUBMITTED", "SUBMITTING", "WAITING", "WAITING_SUBMIT", "SUBMITTED_ALL"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="港股富途模拟盘会话；按预算/亏损约束运行到港股收盘并生成报告")
    parser.add_argument("--max-budget", type=float, default=100000.0)
    parser.add_argument("--max-order-value", type=float, default=20000.0)
    parser.add_argument("--max-loss", type=float, default=2000.0)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--max-symbols", type=int, default=10)
    parser.add_argument("--max-new-orders", type=int, default=5)
    parser.add_argument("--quote-retries", type=int, default=3)
    parser.add_argument("--quote-retry-sleep", type=float, default=5.0)
    parser.add_argument("--force-once", action="store_true", help="只执行一次评估，用于验证")
    return parser


class HkFutuSimSession:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.runs = REPO_ROOT / "state" / "runs"
        self.report_path = self.runs / "hk_futu_sim_session_report.json"
        self.state_path = self.runs / "hk_futu_sim_session_state.json"
        self.quote_client = FutuQuoteClient()
        self.account_provider = FutuAccountProvider()
        self.trade_client = FutuSimTradeClient()
        self.strategy_engine = StrategyEngine(enable_strategy_selection=True)
        self.order_store = OrderStateStore(self.runs / "orders")
        self.order_machine = OrderStateMachine()
        self.orders: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.start_equity = 0.0

    def run(self) -> dict[str, Any]:
        first = self._snapshot()
        self.start_equity = self._equity(first)
        if self.start_equity <= 0:
            self.start_equity = float(self.args.max_budget)
        snapshot = first
        while True:
            now_hk = datetime.now(ZoneInfo("Asia/Hong_Kong"))
            try:
                snapshot = self._snapshot()
                loss = self._current_loss(snapshot)
                if loss >= self.args.max_loss:
                    self.actions.append({"time": datetime.now().isoformat(timespec="seconds"), "action": "stop_new_orders", "reason": "max_loss_reached", "loss": loss})
                    self._reduce_losing_positions(snapshot)
                elif self._is_regular_session(now_hk):
                    self._evaluate_and_trade(snapshot)
                else:
                    self.actions.append({"time": datetime.now().isoformat(timespec="seconds"), "action": "wait", "reason": "outside_regular_session", "hk_time": now_hk.isoformat()})
            except Exception as exc:
                self.actions.append({"time": datetime.now().isoformat(timespec="seconds"), "action": "error", "reason": "loop_exception", "error": str(exc)})

            report = self._write_report(snapshot=snapshot, final=now_hk.time() >= dtime(16, 0) or self.args.force_once)
            if self.args.force_once or now_hk.time() >= dtime(16, 0):
                return report
            time.sleep(max(int(self.args.interval_seconds), 30))

    def _snapshot(self) -> dict[str, Any]:
        account = self.account_provider.get_summary()
        positions = self.trade_client.get_positions()
        symbols = sorted({self._to_vt_symbol(p.get("code") or p.get("symbol", "")) for p in self._hk_positions(positions.get("items", [])) if p.get("qty", 0)})
        watch = self.account_provider.get_watchlist_snapshot(symbols) if symbols else {"items": [], "status": "connected", "message": "no positions"}
        return {"account": asdict(account), "positions": positions, "position_quotes": watch, "time": datetime.now().isoformat(timespec="seconds")}

    def _evaluate_and_trade(self, snapshot: dict[str, Any]) -> None:
        if self._has_open_orders(snapshot):
            self.actions.append({"time": datetime.now().isoformat(timespec="seconds"), "action": "skip", "reason": "open_orders_exist"})
            return
        candidates = self._candidate_rows(snapshot)
        if not candidates:
            self.actions.append({"time": datetime.now().isoformat(timespec="seconds"), "action": "skip", "reason": "no_candidates"})
            return
        quote_rows = self._safe_get_snapshot([row["symbol"] for row in candidates])
        if not quote_rows:
            self.actions.append({"time": datetime.now().isoformat(timespec="seconds"), "action": "skip", "reason": "quote_snapshot_unavailable"})
            return
        quote_map = {self._to_vt_symbol(str(row.get("code", ""))): row for row in quote_rows}
        ranked: list[dict[str, Any]] = []
        for candidate in candidates:
            symbol = candidate["symbol"]
            quote = quote_map.get(symbol)
            if not quote:
                continue
            evaluation = self.strategy_engine.evaluate_candidate(candidate=candidate, quote=quote, flow_divisor=2e9, has_event_catalyst=self._has_catalyst(candidate))
            ranked.append({"candidate": candidate, "quote": quote, "evaluation": evaluation, "score": evaluation.task_score})
        ranked.sort(key=lambda item: item["score"], reverse=True)
        for item in ranked[: self.args.max_new_orders]:
            evaluation = item["evaluation"]
            symbol = item["candidate"]["symbol"]
            price = float(item["quote"].get("ask_price") or item["quote"].get("last_price") or item["quote"].get("price") or 0)
            if price <= 0 or not evaluation.signal.allow_trade:
                continue
            if self._held_qty(snapshot, symbol) > 0:
                continue
            if self._used_budget(snapshot) >= self.args.max_budget:
                break
            order_value = min(self.args.max_order_value, self.args.max_budget - self._used_budget(snapshot))
            qty = int(order_value // price)
            if qty <= 0:
                continue
            self._submit(symbol, "BUY", qty, price * 1.002, evaluation.signal.reason)
            snapshot = self._snapshot()

    def _safe_get_snapshot(self, codes: list[str]) -> list[dict[str, Any]]:
        clean_codes = [code for code in codes if code]
        if not clean_codes:
            return []
        last_error = ""
        for attempt in range(1, max(int(self.args.quote_retries), 1) + 1):
            try:
                return self.quote_client.get_snapshot(clean_codes)
            except Exception as exc:
                last_error = str(exc)
                self.actions.append({"time": datetime.now().isoformat(timespec="seconds"), "action": "quote_retry", "attempt": attempt, "reason": last_error, "symbols": clean_codes})
                time.sleep(max(float(self.args.quote_retry_sleep), 0.0))
        self.actions.append({"time": datetime.now().isoformat(timespec="seconds"), "action": "quote_failed", "reason": last_error, "symbols": clean_codes})
        return []

    def _reduce_losing_positions(self, snapshot: dict[str, Any]) -> None:
        for pos in self._hk_positions(snapshot.get("positions", {}).get("items", [])):
            qty = int(pos.get("can_sell_qty") or 0)
            if qty <= 0:
                continue
            symbol = self._to_vt_symbol(str(pos.get("code") or pos.get("symbol")))
            pnl = (float(pos.get("nominal_price") or 0) - float(pos.get("cost_price") or 0)) * int(pos.get("qty") or 0)
            if pnl >= 0:
                continue
            price = float(pos.get("nominal_price") or pos.get("cost_price") or 0)
            if price > 0:
                self._submit(symbol, "SELL", qty, price * 0.998, "max_loss_reduce_losing_position")

    def _submit(self, symbol: str, side: str, qty: int, price: float, reason: str) -> None:
        request_id = f"hk_futu_sim_{datetime.now().strftime('%Y%m%d%H%M%S')}_{symbol.replace('.', '_')}_{side}"
        try:
            result = self.trade_client.submit_limit_order(symbol, side, qty, price, reason=reason)
            status = self.trade_client.get_order(result.order_id) if result.order_id else None
        except Exception as exc:
            self.actions.append({"time": datetime.now().isoformat(timespec="seconds"), "action": "submit_failed", "symbol": symbol, "side": side, "qty": qty, "reason": str(exc)})
            return
        payload = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "request_id": request_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "input_price": round(price, 4),
            "submitted_price": result.submitted_price,
            "success": result.success,
            "message": result.message,
            "order_id": result.order_id,
            "order_status": result.status,
            "dealt_qty": result.dealt_qty,
            "dealt_avg_price": result.dealt_avg_price,
            "status_check": None if not status else asdict(status),
        }
        self.orders.append(payload)
        self._record_order_state(payload, reason)

    def _record_order_state(self, payload: dict[str, Any], reason: str) -> None:
        if not payload.get("success") or int(payload.get("qty") or 0) <= 0:
            return
        intent = OrderIntent(
            request_id=str(payload["request_id"]),
            symbol=str(payload["symbol"]),
            market="hong_kong",
            strategy_id="hk_futu_sim_session",
            side=str(payload["side"]),
            qty=int(payload["qty"]),
            price=float(payload.get("submitted_price") or payload.get("input_price") or 0),
            target_position_pct=min(float(payload["qty"]) * float(payload.get("submitted_price") or 0) / max(self.args.max_budget, 1.0), 1.0),
            reason=reason,
            signal_snapshot={"futu_sim_submit": payload},
        )
        state = self.order_machine.create(intent)
        state = self.order_machine.transition(state, "validated", note="futu_sim_session")
        state = self.order_machine.transition(state, "risk_checked", note="budget_loss_constraints_checked")
        state = self.order_machine.transition(state, "approved", note="user_requested_futu_sim_session")
        state = self.order_machine.transition(state, "submitted", note="sent_to_futu_sim")
        broker_status = str(payload.get("order_status") or payload.get("status_check", {}).get("status") or "SUBMITTED")
        filled_qty = int(payload.get("dealt_qty") or payload.get("status_check", {}).get("dealt_qty") or 0)
        avg_price = payload.get("dealt_avg_price") or payload.get("status_check", {}).get("dealt_avg_price")
        state = self.order_machine.apply_broker_order(state, broker_order_id=str(payload.get("order_id") or ""), broker_status=broker_status, filled_qty=filled_qty, avg_fill_price=avg_price, snapshot={"futu_sim_session": payload})
        self.order_store.save(state)

    def _candidate_rows(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        rows = UnifiedCandidateProvider(REPO_ROOT).load("hong_kong")[: self.args.max_symbols]
        existing = [self._to_vt_symbol(p.get("code") or p.get("symbol", "")) for p in self._hk_positions(snapshot.get("positions", {}).get("items", [])) if p.get("qty", 0)]
        seen = {row.get("symbol") for row in rows}
        for symbol in [*existing, *FALLBACK_SYMBOLS]:
            normalized = self._to_vt_symbol(symbol)
            if normalized and normalized not in seen:
                rows.append({"symbol": normalized, "market": "hong_kong", "name": normalized, "raw_score": 0.62, "rationale": "existing_or_fallback_hk_candidate"})
                seen.add(normalized)
        return rows[: self.args.max_symbols]

    def _write_report(self, *, snapshot: dict[str, Any], final: bool) -> dict[str, Any]:
        report = {
            "task": "hk_futu_sim_session",
            "started_at": self.started_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "final": final,
            "constraints": {"max_budget": self.args.max_budget, "max_order_value": self.args.max_order_value, "max_loss": self.args.max_loss},
            "start_equity": self.start_equity,
            "current_equity": self._equity(snapshot),
            "current_loss": self._current_loss(snapshot),
            "used_budget": self._used_budget(snapshot),
            "account": snapshot.get("account"),
            "positions": snapshot.get("positions"),
            "orders": self.orders,
            "actions": self.actions[-100:],
        }
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self.state_path.write_text(json.dumps({"started_at": self.started_at, "last_update": report["updated_at"], "orders": self.orders}, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def _equity(self, snapshot: dict[str, Any]) -> float:
        account = snapshot.get("account", {})
        total = float(account.get("total_assets") or 0)
        if total > 0:
            return total
        cash = float(account.get("cash") or 0)
        positions_value = sum(float(p.get("nominal_price") or 0) * int(p.get("qty") or 0) for p in self._hk_positions(snapshot.get("positions", {}).get("items", [])))
        return cash + positions_value

    def _used_budget(self, snapshot: dict[str, Any]) -> float:
        return sum(max(float(p.get("nominal_price") or p.get("cost_price") or 0), 0.0) * max(int(p.get("qty") or 0), 0) for p in self._hk_positions(snapshot.get("positions", {}).get("items", [])))

    def _current_loss(self, snapshot: dict[str, Any]) -> float:
        unrealized_loss = sum(max((float(p.get("cost_price") or 0) - float(p.get("nominal_price") or 0)) * int(p.get("qty") or 0), 0.0) for p in self._hk_positions(snapshot.get("positions", {}).get("items", [])))
        return round(unrealized_loss, 4)

    def _has_open_orders(self, snapshot: dict[str, Any]) -> bool:
        return any(str(o.get("status", "")).upper() in OPEN_ORDER_STATUSES for o in snapshot.get("account", {}).get("orders", []))

    def _held_qty(self, snapshot: dict[str, Any], symbol: str) -> int:
        for p in self._hk_positions(snapshot.get("positions", {}).get("items", [])):
            if self._to_vt_symbol(p.get("code") or p.get("symbol", "")) == symbol:
                return int(p.get("qty") or 0)
        return 0

    def _hk_positions(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if self._to_vt_symbol(row.get("code") or row.get("symbol", "")).endswith(".HK")]

    def _has_catalyst(self, candidate: dict[str, Any]) -> bool:
        text = str(candidate.get("rationale") or "")
        return any(word in text for word in ["主题", "催化", "AI", "业绩", "南下", "突破"])

    def _to_vt_symbol(self, code: str) -> str:
        text = str(code or "")
        if text.startswith("HK."):
            return text.replace("HK.", "", 1) + ".HK"
        if text.startswith("US."):
            return text.replace("US.", "", 1) + ".US"
        if text.endswith(".HK") and len(text.split(".", 1)[0]) < 5:
            left, _ = text.split(".", 1)
            return f"{left.zfill(5)}.HK"
        return text

    def _is_regular_session(self, now_hk: datetime) -> bool:
        if now_hk.weekday() >= 5:
            return False
        current = now_hk.time()
        return dtime(9, 30) <= current < dtime(12, 0) or dtime(13, 0) <= current < dtime(16, 0)


def main() -> None:
    args = build_parser().parse_args()
    report = HkFutuSimSession(args).run()
    print(json.dumps({"report": str(REPO_ROOT / "state" / "runs" / "hk_futu_sim_session_report.json"), "final": report.get("final"), "orders": len(report.get("orders", []))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
