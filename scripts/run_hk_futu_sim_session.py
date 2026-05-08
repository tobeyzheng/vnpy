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
    parser.add_argument("--trend-budget-pct", type=float, default=0.25, help="追趋势资金袖占总预算比例")
    parser.add_argument("--grid-budget-pct", type=float, default=0.25, help="日内做T/网格资金袖占总预算比例")
    parser.add_argument("--trend-min-score", type=float, default=68.0, help="追趋势最低 task_score")
    parser.add_argument("--trend-min-change-pct", type=float, default=0.8, help="追趋势最低日内涨幅")
    parser.add_argument("--trend-max-change-pct", type=float, default=7.0, help="追趋势最高日内涨幅，避免追过热")
    parser.add_argument("--trend-fallback-min-raw-score", type=float, default=0.65, help="change_pct 缺失时趋势试单最低 raw_score")
    parser.add_argument("--trend-fallback-min-turnover", type=float, default=2e9, help="change_pct 缺失时趋势试单最低成交额")
    parser.add_argument("--trend-fallback-order-pct", type=float, default=0.5, help="change_pct 缺失时趋势试单使用单笔上限比例")
    parser.add_argument("--grid-step-pct", type=float, default=1.2, help="网格下跌买入触发幅度")
    parser.add_argument("--grid-take-profit-pct", type=float, default=1.8, help="网格上涨卖出触发幅度")

    parser.add_argument("--grid-trade-pct", type=float, default=0.25, help="每次做T最多处理持仓比例")
    parser.add_argument("--grid-max-order-value", type=float, default=0.0, help="单笔网格买入上限；0 表示 max_order_value * grid_trade_pct")
    parser.add_argument("--max-symbol-value-pct", type=float, default=0.20, help="单标的最大持仓市值占总预算比例")
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
        self.rounds: list[dict[str, Any]] = []
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.start_equity = 0.0

    def _emit(self, event: dict[str, Any]) -> None:
        payload = {"time": datetime.now().isoformat(timespec="seconds"), **event}
        self.actions.append(payload)
        print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)

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
                self._emit({"action": "round_start", "hk_time": now_hk.isoformat(), "current_loss": loss, "used_budget": self._used_budget(snapshot), "equity": self._equity(snapshot)})
                if loss >= self.args.max_loss:
                    self._emit({"action": "stop_new_orders", "reason": "max_loss_reached", "loss": loss})
                    self._reduce_losing_positions(snapshot)
                elif self._is_regular_session(now_hk):
                    self._evaluate_and_trade(snapshot)
                else:
                    self._emit({"action": "wait", "reason": "outside_regular_session", "hk_time": now_hk.isoformat()})
            except Exception as exc:
                self._emit({"action": "error", "reason": "loop_exception", "error": str(exc)})


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
        round_info: dict[str, Any] = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "phase": "evaluate_and_trade",
            "budget_plan": self._budget_plan(snapshot),
            "candidates": [],
            "evaluations": [],
            "grid_evaluations": [],
            "decisions": [],
            "submitted_orders": [],
            "summary": {},
        }
        if self._has_open_orders(snapshot):
            round_info["summary"] = {"decision": "skip", "reason": "open_orders_exist"}
            self.rounds.append(round_info)
            self._emit({"action": "skip", "reason": "open_orders_exist"})
            return

        candidates = self._candidate_rows(snapshot)
        round_info["candidates"] = [{"symbol": row.get("symbol"), "name": row.get("name"), "raw_score": row.get("raw_score"), "rationale": row.get("rationale")} for row in candidates]
        self._emit({"action": "candidate_loaded", "candidate_count": len(candidates), "symbols": [row.get("symbol") for row in candidates]})
        if not candidates and not self._hk_positions(snapshot.get("positions", {}).get("items", [])):
            round_info["summary"] = {"decision": "skip", "reason": "no_candidates_or_positions"}
            self.rounds.append(round_info)
            self._emit({"action": "skip", "reason": "no_candidates_or_positions"})
            return

        quote_symbols = sorted({*[row["symbol"] for row in candidates], *self._held_symbols(snapshot)})
        quote_rows = self._safe_get_snapshot(quote_symbols)
        round_info["quote_count"] = len(quote_rows)
        if not quote_rows:
            round_info["summary"] = {"decision": "skip", "reason": "quote_snapshot_unavailable"}
            self.rounds.append(round_info)
            self._emit({"action": "skip", "reason": "quote_snapshot_unavailable"})
            return

        quote_map = {self._to_vt_symbol(str(row.get("code", ""))): row for row in quote_rows}
        ranked: list[dict[str, Any]] = []
        for candidate in candidates:
            symbol = candidate["symbol"]
            quote = quote_map.get(symbol)
            if not quote:
                round_info["evaluations"].append({"symbol": symbol, "decision": "missing_quote"})
                continue
            evaluation = self.strategy_engine.evaluate_candidate(candidate=candidate, quote=quote, flow_divisor=2e9, has_event_catalyst=self._has_catalyst(candidate))
            strategy_id = self._strategy_id(evaluation)
            change_pct = self._float(quote.get("change_pct"), 0.0)
            change_pct_missing = bool(quote.get("_change_pct_missing"))
            turnover = self._float(quote.get("turnover"), 0.0)
            trend_mode = self._trend_candidate_mode(evaluation, change_pct, change_pct_missing, turnover)
            evaluation_row = {

                "symbol": symbol,
                "name": candidate.get("name"),
                "price": self._quote_price(quote, "BUY"),
                "change_pct": quote.get("change_pct"),
                "change_pct_missing": change_pct_missing,
                "turnover": quote.get("turnover"),

                "task_score": evaluation.task_score,
                "raw_score": evaluation.raw_score,
                "entry_action": evaluation.entry_timing.action,
                "entry_reason": evaluation.entry_timing.reason,
                "allow_trade": evaluation.signal.allow_trade,
                "target_position_pct": evaluation.signal.target_position_pct,
                "strategy_id": strategy_id,
                "trend_mode": trend_mode,
                "trend_eligible": trend_mode != "none",
                "strategy_selection": evaluation.metadata.get("strategy_selection", {}),

                "risk_flags": evaluation.signal.risk_flags,
            }
            round_info["evaluations"].append(evaluation_row)
            ranked.append({"candidate": candidate, "quote": quote, "evaluation": evaluation, "score": evaluation.task_score, "strategy_id": strategy_id, "change_pct": change_pct, "change_pct_missing": change_pct_missing, "turnover": turnover, "trend_mode": trend_mode})

        ranked.sort(key=lambda item: item["score"], reverse=True)
        top_symbols = [item["candidate"]["symbol"] for item in ranked[: self.args.max_new_orders]]
        self._emit({"action": "evaluation_done", "evaluated_count": len(round_info["evaluations"]), "top_symbols": top_symbols})

        submitted_symbols: set[str] = set()
        order_slots = max(int(self.args.max_new_orders), 0)
        order_slots = self._run_grid_sleeve(snapshot, quote_map, round_info, submitted_symbols, order_slots)
        if round_info["submitted_orders"]:
            snapshot = self._snapshot()
            round_info["budget_plan_after_grid"] = self._budget_plan(snapshot)

        order_slots = self._run_trend_sleeve(snapshot, ranked, round_info, submitted_symbols, order_slots)
        if len(round_info["submitted_orders"]) > 0:
            snapshot = self._snapshot()
            round_info["budget_plan_after_trend"] = self._budget_plan(snapshot)

        self._run_core_sleeve(snapshot, ranked, round_info, submitted_symbols, order_slots)

        submitted_count = len(round_info["submitted_orders"])
        round_info["summary"] = {
            "decision": "submitted" if submitted_count else "no_submit",
            "submitted_count": submitted_count,
            "decision_count": len(round_info["decisions"]),
            "grid_decision_count": len(round_info["grid_evaluations"]),
            "budget_plan": self._budget_plan(self._snapshot()),
        }
        self.rounds.append(round_info)
        self._emit({"action": "round_decision_done", **round_info["summary"]})

    def _run_grid_sleeve(self, snapshot: dict[str, Any], quote_map: dict[str, dict[str, Any]], round_info: dict[str, Any], submitted_symbols: set[str], order_slots: int) -> int:
        if order_slots <= 0 or self._grid_budget_cap() <= 0:
            return order_slots
        grid_remaining = self._remaining_sleeve_buy_budget("grid", self._grid_budget_cap())
        for pos in self._hk_positions(snapshot.get("positions", {}).get("items", [])):
            if order_slots <= 0:
                break
            symbol = self._to_vt_symbol(pos.get("code") or pos.get("symbol", ""))
            quote = quote_map.get(symbol)
            price = self._quote_price(quote or {}, "SELL") or self._float(pos.get("nominal_price"), 0.0)
            cost = self._float(pos.get("cost_price"), 0.0)
            qty = int(pos.get("qty") or 0)
            can_sell_qty = int(pos.get("can_sell_qty") or 0)
            lot = self._lot_size(quote or {})
            pnl_pct = (price / cost - 1.0) * 100 if price > 0 and cost > 0 else 0.0
            grid_row = {"symbol": symbol, "price": price, "cost_price": cost, "qty": qty, "can_sell_qty": can_sell_qty, "pnl_pct": round(pnl_pct, 4), "lot_size": lot}
            if price <= 0 or qty <= 0:
                grid_row.update({"final_decision": "skip", "reason": "invalid_position_price_or_qty"})
                round_info["grid_evaluations"].append(grid_row)
                continue
            if pnl_pct >= float(self.args.grid_take_profit_pct) and can_sell_qty > 0:
                sell_qty = self._round_lot(min(can_sell_qty, max(lot, int(can_sell_qty * self._bounded_pct(self.args.grid_trade_pct, 0.25)))), lot)
                if sell_qty <= 0:
                    grid_row.update({"final_decision": "skip", "reason": "sell_qty_zero"})
                    round_info["grid_evaluations"].append(grid_row)
                    continue
                grid_row.update({"final_decision": "submit_sell", "reason": "grid_take_profit", "qty": sell_qty, "trigger_pct": self.args.grid_take_profit_pct})
                submitted = self._submit(symbol, "SELL", sell_qty, price * 0.998, "grid_take_profit", sleeve="grid")
                if submitted:
                    round_info["submitted_orders"].append(submitted)
                    submitted_symbols.add(symbol)
                    order_slots -= 1
                round_info["grid_evaluations"].append(grid_row)
                continue
            if pnl_pct <= -float(self.args.grid_step_pct) and grid_remaining > 0:
                total_remaining = self._remaining_total_budget(snapshot)
                symbol_remaining = self._remaining_symbol_budget(snapshot, symbol, price)
                order_value = min(self._grid_order_value_cap(), grid_remaining, total_remaining, symbol_remaining)
                buy_qty = self._round_lot(int(order_value // price), lot)
                grid_row.update({"order_value": round(order_value, 4), "qty": buy_qty, "trigger_pct": -float(self.args.grid_step_pct)})
                if buy_qty <= 0:
                    grid_row.update({"final_decision": "skip", "reason": "grid_buy_qty_zero"})
                    round_info["grid_evaluations"].append(grid_row)
                    continue
                grid_row.update({"final_decision": "submit_buy", "reason": "grid_pullback_buy"})
                submitted = self._submit(symbol, "BUY", buy_qty, price * 1.002, "grid_pullback_buy", sleeve="grid")
                if submitted:
                    round_info["submitted_orders"].append(submitted)
                    submitted_symbols.add(symbol)
                    grid_remaining = max(grid_remaining - buy_qty * price, 0.0)
                    order_slots -= 1
                round_info["grid_evaluations"].append(grid_row)
                continue
            grid_row.update({"final_decision": "hold", "reason": "inside_grid_band", "buy_trigger_pct": -float(self.args.grid_step_pct), "sell_trigger_pct": float(self.args.grid_take_profit_pct)})
            round_info["grid_evaluations"].append(grid_row)
        self._emit({"action": "grid_evaluation_done", "evaluated_count": len(round_info["grid_evaluations"]), "remaining_grid_budget": round(grid_remaining, 4), "remaining_order_slots": order_slots})
        return order_slots

    def _run_trend_sleeve(self, snapshot: dict[str, Any], ranked: list[dict[str, Any]], round_info: dict[str, Any], submitted_symbols: set[str], order_slots: int) -> int:
        trend_remaining = self._remaining_sleeve_buy_budget("trend", self._trend_budget_cap())
        for item in ranked:
            if order_slots <= 0 or trend_remaining <= 0:
                break
            evaluation = item["evaluation"]
            symbol = item["candidate"]["symbol"]
            price = self._quote_price(item["quote"], "BUY")
            trend_mode = str(item.get("trend_mode") or "none")
            decision = {"sleeve": "trend", "symbol": symbol, "score": evaluation.task_score, "strategy_id": item.get("strategy_id"), "change_pct": item.get("change_pct"), "change_pct_missing": item.get("change_pct_missing"), "trend_mode": trend_mode, "allow_trade": evaluation.signal.allow_trade, "price": price}

            if symbol in submitted_symbols:
                decision.update({"final_decision": "skip", "reason": "already_submitted_this_round"})
                round_info["decisions"].append(decision)
                continue
            if trend_mode == "none":
                decision.update({"final_decision": "skip", "reason": "not_trend_eligible"})
                round_info["decisions"].append(decision)
                continue
            order_cap = float(self.args.max_order_value)
            if trend_mode == "fallback_missing_change_pct":
                order_cap *= self._bounded_pct(self.args.trend_fallback_order_pct, 0.5)
            order_value = min(order_cap, trend_remaining, self._remaining_total_budget(snapshot), self._remaining_symbol_budget(snapshot, symbol, price))

            qty = self._round_lot(int(order_value // price) if price > 0 else 0, self._lot_size(item["quote"]))
            decision.update({"order_value": round(order_value, 4), "qty": qty, "trend_remaining_before": round(trend_remaining, 4)})
            if price <= 0 or qty <= 0:
                decision.update({"final_decision": "skip", "reason": "trend_qty_zero_or_invalid_price"})
                round_info["decisions"].append(decision)
                continue
            submit_reason = f"trend_sleeve:{trend_mode}:{evaluation.signal.reason}"
            decision.update({"final_decision": "submit", "reason": submit_reason})
            submitted = self._submit(symbol, "BUY", qty, price * 1.002, submit_reason, sleeve="trend")

            if submitted:
                round_info["submitted_orders"].append(submitted)
                submitted_symbols.add(symbol)
                trend_remaining = max(trend_remaining - qty * price, 0.0)
                order_slots -= 1
            round_info["decisions"].append(decision)
        self._emit({"action": "trend_sleeve_done", "remaining_trend_budget": round(trend_remaining, 4), "remaining_order_slots": order_slots})
        return order_slots

    def _run_core_sleeve(self, snapshot: dict[str, Any], ranked: list[dict[str, Any]], round_info: dict[str, Any], submitted_symbols: set[str], order_slots: int) -> int:
        core_remaining = self._remaining_sleeve_buy_budget("core", self._core_budget_cap())
        for item in ranked:
            if order_slots <= 0 or core_remaining <= 0:
                break
            evaluation = item["evaluation"]
            symbol = item["candidate"]["symbol"]
            price = self._quote_price(item["quote"], "BUY")
            decision = {"sleeve": "core", "symbol": symbol, "score": evaluation.task_score, "allow_trade": evaluation.signal.allow_trade, "price": price}
            if symbol in submitted_symbols:
                decision.update({"final_decision": "skip", "reason": "already_submitted_this_round"})
                round_info["decisions"].append(decision)
                continue
            if price <= 0:
                decision.update({"final_decision": "skip", "reason": "invalid_price"})
                round_info["decisions"].append(decision)
                continue
            if not evaluation.signal.allow_trade:
                decision.update({"final_decision": "skip", "reason": "strategy_blocked", "entry_action": evaluation.entry_timing.action})
                round_info["decisions"].append(decision)
                continue
            held_qty = self._held_qty(snapshot, symbol)
            if held_qty > 0:
                decision.update({"final_decision": "skip", "reason": "already_held", "held_qty": held_qty})
                round_info["decisions"].append(decision)
                continue
            order_value = min(self.args.max_order_value, core_remaining, self._remaining_total_budget(snapshot), self._remaining_symbol_budget(snapshot, symbol, price))
            qty = self._round_lot(int(order_value // price), self._lot_size(item["quote"]))
            decision.update({"order_value": round(order_value, 4), "qty": qty, "core_remaining_before": round(core_remaining, 4)})
            if qty <= 0:
                decision.update({"final_decision": "skip", "reason": "qty_zero"})
                round_info["decisions"].append(decision)
                continue
            decision.update({"final_decision": "submit", "reason": f"core_sleeve:{evaluation.signal.reason}"})
            submitted = self._submit(symbol, "BUY", qty, price * 1.002, f"core_sleeve:{evaluation.signal.reason}", sleeve="core")
            if submitted:
                round_info["submitted_orders"].append(submitted)
                submitted_symbols.add(symbol)
                core_remaining = max(core_remaining - qty * price, 0.0)
                order_slots -= 1
            round_info["decisions"].append(decision)
        self._emit({"action": "core_sleeve_done", "remaining_core_budget": round(core_remaining, 4), "remaining_order_slots": order_slots})
        return order_slots





    def _budget_plan(self, snapshot: dict[str, Any]) -> dict[str, float]:

        used = self._used_budget(snapshot)
        trend_cap = self._trend_budget_cap()
        grid_cap = self._grid_budget_cap()
        core_cap = self._core_budget_cap()
        return {
            "max_budget": float(self.args.max_budget),
            "used_budget": round(used, 4),
            "remaining_total": round(max(float(self.args.max_budget) - used, 0.0), 4),
            "core_cap": round(core_cap, 4),
            "trend_cap": round(trend_cap, 4),
            "grid_cap": round(grid_cap, 4),
            "core_session_buy": round(self._session_buy_value("core"), 4),
            "trend_session_buy": round(self._session_buy_value("trend"), 4),
            "grid_session_buy": round(self._session_buy_value("grid"), 4),
            "grid_session_sell": round(self._session_sell_value("grid"), 4),
        }

    def _core_budget_cap(self) -> float:
        trend = self._bounded_pct(self.args.trend_budget_pct, 0.25)
        grid = self._bounded_pct(self.args.grid_budget_pct, 0.25)
        return max(float(self.args.max_budget) * max(1.0 - trend - grid, 0.0), 0.0)

    def _trend_budget_cap(self) -> float:
        return max(float(self.args.max_budget) * self._bounded_pct(self.args.trend_budget_pct, 0.25), 0.0)

    def _grid_budget_cap(self) -> float:
        return max(float(self.args.max_budget) * self._bounded_pct(self.args.grid_budget_pct, 0.25), 0.0)

    def _grid_order_value_cap(self) -> float:
        configured = float(self.args.grid_max_order_value or 0.0)
        if configured > 0:
            return min(configured, float(self.args.max_order_value))
        return max(float(self.args.max_order_value) * self._bounded_pct(self.args.grid_trade_pct, 0.25), 0.0)

    def _remaining_total_budget(self, snapshot: dict[str, Any]) -> float:
        return max(float(self.args.max_budget) - self._used_budget(snapshot), 0.0)

    def _remaining_sleeve_buy_budget(self, sleeve: str, cap: float) -> float:
        return max(float(cap) - self._session_buy_value(sleeve), 0.0)

    def _remaining_symbol_budget(self, snapshot: dict[str, Any], symbol: str, price: float) -> float:
        symbol_cap = max(float(self.args.max_budget) * self._bounded_pct(self.args.max_symbol_value_pct, 0.20), 0.0)
        current_value = self._held_qty(snapshot, symbol) * max(float(price), 0.0)
        return max(symbol_cap - current_value, 0.0)

    def _session_buy_value(self, sleeve: str) -> float:
        return sum(self._order_value(order) for order in self.orders if order.get("sleeve") == sleeve and str(order.get("side", "")).upper() == "BUY" and order.get("success"))

    def _session_sell_value(self, sleeve: str) -> float:
        return sum(self._order_value(order) for order in self.orders if order.get("sleeve") == sleeve and str(order.get("side", "")).upper() == "SELL" and order.get("success"))

    def _order_value(self, order: dict[str, Any]) -> float:
        return max(float(order.get("submitted_price") or order.get("input_price") or 0.0), 0.0) * max(int(order.get("qty") or 0), 0)

    def _strategy_id(self, evaluation: Any) -> str:
        selection = evaluation.metadata.get("strategy_selection", {}) if isinstance(evaluation.metadata, dict) else {}
        return str(selection.get("strategy_id") or evaluation.entry_timing.action or "")

    def _trend_candidate_mode(self, evaluation: Any, change_pct: float, change_pct_missing: bool, turnover: float) -> str:
        strategy_id = self._strategy_id(evaluation)
        if (
            evaluation.signal.allow_trade
            and strategy_id in {"trend_following", "breakout_momentum"}
            and float(evaluation.task_score) >= float(self.args.trend_min_score)
            and float(self.args.trend_min_change_pct) <= float(change_pct) <= float(self.args.trend_max_change_pct)
        ):
            return "rules_confirmed"
        if (
            change_pct_missing
            and float(evaluation.task_score) >= float(self.args.trend_min_score)
            and float(evaluation.raw_score) >= float(self.args.trend_fallback_min_raw_score)
            and float(turnover) >= float(self.args.trend_fallback_min_turnover)
        ):
            return "fallback_missing_change_pct"
        return "none"

    def _held_symbols(self, snapshot: dict[str, Any]) -> list[str]:

        return [self._to_vt_symbol(p.get("code") or p.get("symbol", "")) for p in self._hk_positions(snapshot.get("positions", {}).get("items", [])) if int(p.get("qty") or 0) > 0]

    def _quote_price(self, quote: dict[str, Any], side: str) -> float:
        if side.upper() == "SELL":
            return self._float(quote.get("bid_price") or quote.get("last_price") or quote.get("price"), 0.0)
        return self._float(quote.get("ask_price") or quote.get("last_price") or quote.get("price"), 0.0)

    def _lot_size(self, quote: dict[str, Any]) -> int:
        try:
            lot = int(float(quote.get("lot_size") or 100))
        except (TypeError, ValueError):
            lot = 100
        return max(lot, 1)

    def _round_lot(self, qty: int, lot: int) -> int:
        lot = max(int(lot), 1)
        return max(int(qty), 0) // lot * lot

    def _bounded_pct(self, value: Any, default: float) -> float:
        pct = self._float(value, default)
        return min(max(pct, 0.0), 1.0)

    def _float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _normalize_quote_row(self, row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        last_price = self._float(item.get("last_price") or item.get("price"), 0.0)
        prev_close = self._float(item.get("prev_close_price") or item.get("prev_close"), 0.0)
        if item.get("price") in {None, ""} and last_price > 0:
            item["price"] = last_price
        if item.get("change_pct") in {None, ""}:
            if last_price > 0 and prev_close > 0:
                item["change_pct"] = round((last_price / prev_close - 1.0) * 100, 4)
                item["_change_pct_missing"] = False
            else:
                item["change_pct"] = None
                item["_change_pct_missing"] = True
        else:
            item["change_pct"] = self._float(item.get("change_pct"), 0.0)
            item["_change_pct_missing"] = False
        return item

    def _safe_get_snapshot(self, codes: list[str]) -> list[dict[str, Any]]:
        clean_codes = [code for code in codes if code]

        if not clean_codes:
            return []
        last_error = ""
        for attempt in range(1, max(int(self.args.quote_retries), 1) + 1):
            try:
                return [self._normalize_quote_row(row) for row in self.quote_client.get_snapshot(clean_codes)]
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

    def _submit(self, symbol: str, side: str, qty: int, price: float, reason: str, *, sleeve: str = "core") -> dict[str, Any] | None:
        request_id = f"hk_futu_sim_{datetime.now().strftime('%Y%m%d%H%M%S')}_{symbol.replace('.', '_')}_{side}"
        self._emit({"action": "submit_attempt", "request_id": request_id, "sleeve": sleeve, "symbol": symbol, "side": side, "qty": qty, "price": round(price, 4), "reason": reason})

        try:
            result = self.trade_client.submit_limit_order(symbol, side, qty, price, reason=reason)
            status = self.trade_client.get_order(result.order_id) if result.order_id else None
        except Exception as exc:
            self._emit({"action": "submit_failed", "request_id": request_id, "sleeve": sleeve, "symbol": symbol, "side": side, "qty": qty, "reason": str(exc)})

            return None
        payload = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "request_id": request_id,
            "sleeve": sleeve,
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
        self._emit({"action": "submit_result", "request_id": request_id, "sleeve": sleeve, "symbol": symbol, "success": result.success, "message": result.message, "order_id": result.order_id, "order_status": result.status, "dealt_qty": result.dealt_qty})

        self._record_order_state(payload, reason)
        return payload


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
            "constraints": {
                "max_budget": self.args.max_budget,
                "max_order_value": self.args.max_order_value,
                "max_loss": self.args.max_loss,
                "trend_budget_pct": self.args.trend_budget_pct,
                "grid_budget_pct": self.args.grid_budget_pct,
                "trend_min_score": self.args.trend_min_score,
                "trend_min_change_pct": self.args.trend_min_change_pct,
                "trend_max_change_pct": self.args.trend_max_change_pct,
                "trend_fallback_min_raw_score": self.args.trend_fallback_min_raw_score,
                "trend_fallback_min_turnover": self.args.trend_fallback_min_turnover,
                "trend_fallback_order_pct": self.args.trend_fallback_order_pct,
                "grid_step_pct": self.args.grid_step_pct,
                "grid_take_profit_pct": self.args.grid_take_profit_pct,

                "grid_trade_pct": self.args.grid_trade_pct,
                "grid_max_order_value": self.args.grid_max_order_value,
                "max_symbol_value_pct": self.args.max_symbol_value_pct,
            },
            "budget_plan": self._budget_plan(snapshot),
            "start_equity": self.start_equity,
            "current_equity": self._equity(snapshot),
            "current_loss": self._current_loss(snapshot),

            "used_budget": self._used_budget(snapshot),
            "account": snapshot.get("account"),
            "positions": snapshot.get("positions"),
            "orders": self.orders,
            "actions": self.actions[-200:],
            "rounds": self.rounds[-20:],
            "latest_round": self.rounds[-1] if self.rounds else None,
        }
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self.state_path.write_text(json.dumps({"started_at": self.started_at, "last_update": report["updated_at"], "orders": self.orders, "latest_round": report["latest_round"]}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

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
