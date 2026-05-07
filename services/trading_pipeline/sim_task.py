from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from services.futu_account import FutuAccountProvider, FutuQuoteClient
from services.sim_account import SimAccountStore, SimTradingEngine
from services.strategy.candidate_provider import UnifiedCandidateProvider
from services.strategy.market_rules import get_market_rules
from services.strategy.raw_score import RawScoreEngine, RawScoreFeatures
from services.strategy.risk_guard import RiskGuard
from services.strategy.timing import EntryTimingEngine


@dataclass(frozen=True)
class MarketSimTaskConfig:
    market: str
    task_name: str
    account_filename: str
    report_filename: str
    budget_per_trade: float
    lot_size_default: int
    quote_prefix: str
    symbol_suffix: str
    flow_divisor: float
    catalyst_keywords: tuple[str, ...]
    affordability_label: str
    max_candidates: int = 30
    max_selected: int = 3


class MultiMarketSimTradingPipeline:
    def __init__(self, repo_root: Path, config: MarketSimTaskConfig):
        self.repo_root = repo_root
        self.config = config
        self.account_path = repo_root / "state" / "runs" / config.account_filename
        self.report_path = repo_root / "state" / "runs" / config.report_filename
        self.account_store = SimAccountStore(self.account_path)
        self.engine = SimTradingEngine(lot_size_default=config.lot_size_default)
        self.quote_client = FutuQuoteClient()
        self.account_provider = FutuAccountProvider()
        self.raw_score_engine = RawScoreEngine()
        self.entry_engine = EntryTimingEngine()
        self.risk_guard = RiskGuard()

    def run(self) -> dict[str, Any]:
        account = self.account_store.load()
        candidates = UnifiedCandidateProvider(self.repo_root).load(self.config.market)[: self.config.max_candidates]
        codes = [c["symbol"] for c in candidates]
        snapshot = self._get_watchlist_snapshot(codes)
        raw_rows = self._get_raw_rows(codes)
        raw_map = self._build_symbol_map(raw_rows, "code")
        quote_map = self._build_symbol_map(snapshot.get("items", []), "code")

        filtered_out: list[dict[str, Any]] = []
        task_candidate_pool = self._build_candidate_pool(candidates, quote_map, raw_map, filtered_out)
        task_candidate_pool.sort(key=lambda x: x["task_score"], reverse=True)
        selected = task_candidate_pool[: self.config.max_selected]

        actions = self._execute_selected(account, selected)
        self.engine.mark_to_market(account, {k: float(v["price"]) for k, v in quote_map.items() if v.get("price") is not None})
        self.account_store.save(account)

        report = {
            "task": self.config.task_name,
            "market": self.config.market,
            "cash": account.cash,
            "nav": account.nav,
            "positions": [asdict(p) for p in account.positions],
            "orders": [asdict(o) for o in account.orders[-10:]],
            "actions": actions,
            "task_candidate_pool": selected,
            "filtered_out": filtered_out,
            "snapshot_status": snapshot.get("status"),
            "snapshot_message": snapshot.get("message"),
        }
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def _get_watchlist_snapshot(self, codes: list[str]) -> dict[str, Any]:
        if not codes:
            return {"items": [], "status": "connected", "message": "no candidates"}
        return self.account_provider.get_watchlist_snapshot(codes)

    def _get_raw_rows(self, codes: list[str]) -> list[dict[str, Any]]:
        if not codes:
            return []
        try:
            return self.quote_client.get_snapshot(codes)
        except Exception:
            return []

    def _build_symbol_map(self, rows: list[dict[str, Any]], code_key: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row.get(code_key, ""))
            symbol = self._futu_code_to_symbol(code)
            if symbol and row.get("price", row.get("last_price")) is not None:
                result[symbol] = row
        return result

    def _futu_code_to_symbol(self, code: str) -> str:
        prefix = f"{self.config.quote_prefix}."
        suffix = f".{self.config.symbol_suffix}"
        if code.startswith(prefix):
            return f"{code.replace(prefix, '', 1)}{suffix}"
        if code.endswith(suffix):
            return code
        return code

    def _build_candidate_pool(self, candidates: list[dict[str, Any]], quote_map: dict[str, dict[str, Any]], raw_map: dict[str, dict[str, Any]], filtered_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
        task_candidate_pool: list[dict[str, Any]] = []
        market_rules = get_market_rules(self.config.market)
        for candidate in candidates:
            symbol = candidate["symbol"]
            item = quote_map.get(symbol)
            raw = raw_map.get(symbol)
            if item is None or raw is None:
                filtered_out.append({"symbol": symbol, "reason": "missing quote"})
                continue

            price = float(item["price"])
            lot_size = self._resolve_lot_size(raw)
            min_cost = self.engine.min_lot_cost(price, lot_size=lot_size)
            if not self.engine.is_affordable(price, self.config.budget_per_trade, lot_size=lot_size):
                filtered_out.append({
                    "symbol": symbol,
                    "reason": f"{self.config.affordability_label} cost {min_cost:.2f} exceeds budget {self.config.budget_per_trade:.2f}",
                    "lot_size": lot_size,
                })
                continue

            legacy = float(candidate.get("raw_score", 0.5) or 0.5)
            change_pct = float(item.get("change_pct") or 0)
            turnover = float(item.get("turnover") or 0)
            has_catalyst = self._has_event_catalyst(candidate)
            features = RawScoreFeatures(
                trend_score=legacy,
                momentum_score=max(0.0, min(1.0, 0.5 + change_pct / 20.0)),
                flow_score=max(0.0, min(1.0, turnover / self.config.flow_divisor)),
                quality_score=self._quality_score(candidate),
                event_score=0.75 if has_catalyst else 0.5,
                risk_penalty=0.65 if abs(change_pct) > 6 else 0.35,
                legacy_score=legacy,
            )
            raw_score_v2 = self.raw_score_engine.score(features)
            timing = self.entry_engine.decide(
                trend_score=raw_score_v2,
                rsi=50 + min(35, abs(change_pct) * 4),
                has_event_catalyst=has_catalyst,
                near_resistance=abs(change_pct) > 4,
                moving_average_bullish=change_pct > 0,
            )
            score = raw_score_v2 * 100
            score += max(0.0, 10 - abs(change_pct))
            score += min(10.0, turnover / 1e9)
            score += 3.0 if timing.action != "watch_only" else -4.0
            task_candidate_pool.append({
                "symbol": symbol,
                "name": candidate.get("name"),
                "price": price,
                "lot_size": lot_size,
                "change_pct": item.get("change_pct"),
                "turnover": item.get("turnover"),
                "task_score": round(score, 2),
                "rationale": candidate.get("rationale", ""),
                "raw_score_v2": raw_score_v2,
                "entry_action": timing.action,
                "entry_reason": timing.reason,
                "invalidator": timing.invalidator,
                "suggested_size_pct": timing.suggested_size_pct,
                "market_currency": market_rules.currency,
            })
        return task_candidate_pool

    def _resolve_lot_size(self, raw: dict[str, Any]) -> int:
        if self.config.market == "hong_kong":
            return int(raw.get("lot_size") or self.config.lot_size_default)
        return self.config.lot_size_default

    def _quality_score(self, candidate: dict[str, Any]) -> float:
        signals = candidate.get("signals") or [{"score": 0.5}]
        return min(1.0, max(float(s.get("score", 0.5) or 0.5) for s in signals))

    def _has_event_catalyst(self, candidate: dict[str, Any]) -> bool:
        rationale = candidate.get("rationale") or ""
        return any(keyword in rationale for keyword in self.config.catalyst_keywords)

    def _execute_selected(self, account: Any, selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for row in selected:
            if row.get("entry_action") == "watch_only":
                actions.append({"symbol": row["symbol"], "action": "watch_only", "reason": row.get("entry_reason")})
                continue

            sized_budget = max(
                float(row["price"]) * int(row["lot_size"]),
                self.config.budget_per_trade * max(0.25, float(row.get("suggested_size_pct", 0.0) or 0.0)),
            )
            guard = self.risk_guard.can_open(account, symbol=row["symbol"], est_cost=sized_budget)
            if self.engine.can_open(account, sized_budget) and guard.allowed:
                order = self.engine.place_buy(account, row["symbol"], float(row["price"]), row.get("rationale", ""), sized_budget, lot_size=int(row["lot_size"]))
                actions.append({"symbol": row["symbol"], "action": order.status, "qty": order.qty, "price": row["price"], "lot_size": row["lot_size"], "reason": order.reason})
            else:
                actions.append({"symbol": row["symbol"], "action": "blocked", "reason": guard.reason if not guard.allowed else "risk/budget limit"})
        return actions
