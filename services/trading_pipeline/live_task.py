from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


from execution.live_bridge.models import LiveOrderRequest
from execution.vnpy_bridge import VnpyEventRecorder, VnpyExecutor, VnpyGatewayEventBridge
from services.common import OrderIntent

from services.execution_guard.idempotency import OrderIdempotencyGuard
from services.execution_guard.live_context import LiveRiskContextBuilder
from services.execution_guard.live_gate import LiveExecutionGate
from services.execution_guard.precheck import SubmitPrecheck
from services.execution_guard.reconciliation import ReconciliationGuard
from services.futu_account import FutuAccountProvider, FutuQuoteClient
from services.futu_opend import OpenDConfig
from services.risk_engine import LiveRiskGuard

from services.strategy.candidate_provider import UnifiedCandidateProvider
from services.strategy.engine import StrategyEngine
from services.strategy.external_selection import ExternalStrategySelectionStore
from services.strategy.selection_store import StrategySelectionStore
from services.trade_state import OrderStateStore




@dataclass(frozen=True)
class LiveTaskConfig:
    market: str
    task_name: str
    report_filename: str
    budget_per_trade: float
    lot_size_default: int
    quote_prefix: str
    symbol_suffix: str
    flow_divisor: float
    catalyst_keywords: tuple[str, ...]
    max_candidates: int = 30
    max_selected: int = 3
    max_order_value: float | None = None
    limit_price_buffer_pct: float = 0.0
    reconciliation_required: bool = True
    reconciliation_max_age_minutes: int = 60
    reconciliation_filename: str = "futu_live_position_reconcile.json"
    strategy_selection_enabled: bool = True
    approval_required: bool = True
    approval_env_var_name: str = "VNPY_LIVE_APPROVED"
    live_submit_enabled: bool = False
    env_var_name: str = "VNPY_LIVE_SUBMIT"
    gateway_name: str = "FUTU"
    gateway_env: str = "REAL"
    gateway_password_env_var_name: str = "FUTU_TRADE_UNLOCK_PASSWORD"
    gateway_connect_wait_seconds: float = 2.0



class LiveTradingPipeline:
    def __init__(self, repo_root: Path, config: LiveTaskConfig, *, live_submit: bool = False):
        self.repo_root = repo_root
        self.config = config
        self.live_submit_requested = bool(live_submit)
        self.live_submit_block_reasons = self._live_submit_block_reasons(live_submit)
        self.live_submit = bool(live_submit and not self.live_submit_block_reasons)
        self.report_path = repo_root / "state" / "runs" / config.report_filename

        self.quote_client = FutuQuoteClient()
        self.account_provider = FutuAccountProvider()
        self.strategy_engine = StrategyEngine(enable_strategy_selection=config.strategy_selection_enabled)
        self.order_store = OrderStateStore(repo_root / "state" / "runs" / "orders")
        self.idempotency_guard = OrderIdempotencyGuard(self.order_store)
        self.risk_context_builder = LiveRiskContextBuilder(self.order_store)
        self.external_selection_store = ExternalStrategySelectionStore(repo_root)
        self.selection_store = StrategySelectionStore(repo_root / "state" / "runs" / "strategy_selection")
        limits_path = repo_root / "configs" / "risk" / "live_risk_limits.yaml"
        limits = self._load_simple_limits(limits_path) if limits_path.exists() else {}

        if config.max_order_value is not None:
            limits = {**limits, "max_order_value": config.max_order_value}
        self.live_gate = LiveExecutionGate(SubmitPrecheck(), LiveRiskGuard(limits))
        self.reconciliation_guard = ReconciliationGuard(
            repo_root / "state" / "runs" / config.reconciliation_filename,
            max_age_minutes=config.reconciliation_max_age_minutes,
            fail_closed=True,

        ) if config.reconciliation_required else None
        self.event_engine: EventEngine | None = None
        self.main_engine: MainEngine | None = None
        self.event_recorder: VnpyEventRecorder | None = None
        self.gateway_event_bridge: VnpyGatewayEventBridge | None = None

    def run(self) -> dict[str, Any]:
        candidates = UnifiedCandidateProvider(self.repo_root).load(self.config.market)[: max(self.config.max_candidates, 0)]
        codes = [c["symbol"] for c in candidates]
        quote_rows = self._get_quote_rows(codes)
        quote_map = self._build_symbol_map(quote_rows, "code")
        account_summary = self.account_provider.get_summary()
        pool = self._build_candidate_pool(candidates, quote_map, account_summary)
        pool.sort(key=lambda row: row["task_score"], reverse=True)
        selected = pool[: max(self.config.max_selected, 0)]

        executor = self._build_executor() if self.live_submit else VnpyExecutor(self.repo_root / "state" / "runs", mode="paper")
        states = [executor.execute_intent(row["order_intent"]) for row in selected if row.get("order_intent")]
        report = {
            "task": self.config.task_name,
            "market": self.config.market,
            "live_submit_requested": self.live_submit_requested,
            "live_submit_effective": self.live_submit,
            "live_submit_enabled": self.config.live_submit_enabled,
            "live_submit_block_reasons": self.live_submit_block_reasons,
            "env_var_required": self.config.env_var_name,

            "risk_config": {
                "budget_per_trade": self.config.budget_per_trade,
                "max_order_value": self.config.max_order_value,
                "max_selected": self.config.max_selected,
                "reconciliation_max_age_minutes": self.config.reconciliation_max_age_minutes,
                "limit_price_buffer_pct": self.config.limit_price_buffer_pct,
                "approval_required": self.config.approval_required,
                "approval_env_var_required": self.config.approval_env_var_name,
                "reconciliation_file": self.config.reconciliation_filename,
                "gateway_name": self.config.gateway_name,
                "gateway_env": self.config.gateway_env,
            },

            "account_status": account_summary.status,
            "account_message": account_summary.message,
            "selected": [{k: v for k, v in row.items() if k != "order_intent"} for row in selected],
            "order_states": [asdict(state) for state in states],
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.main_engine:
            self.main_engine.close()
        return report

    def _build_executor(self) -> VnpyExecutor:
        from vnpy.event import EventEngine
        from vnpy.trader.engine import MainEngine
        from vnpy.trader.event import EVENT_ORDER, EVENT_TRADE
        from vnpy_futu import FutuGateway

        self.event_engine = EventEngine()

        self.main_engine = MainEngine(self.event_engine)
        self.main_engine.add_gateway(FutuGateway, self.config.gateway_name)

        self.event_recorder = VnpyEventRecorder(self.repo_root / "state" / "runs")
        self.event_recorder.register(self.event_engine)
        self.gateway_event_bridge = VnpyGatewayEventBridge(self.repo_root / "state" / "runs")
        self.event_engine.register(EVENT_ORDER, lambda event: self.gateway_event_bridge.order_event_to_state(event.data))
        self.event_engine.register(EVENT_TRADE, lambda event: self.gateway_event_bridge.trade_event_to_state(event.data))
        futu_market = "US" if self.config.market == "us" else "HK" if self.config.market == "hong_kong" else "CN"
        opend = OpenDConfig()
        self.main_engine.connect({
            "密码": os.environ.get(self.config.gateway_password_env_var_name, ""),
            "地址": opend.host,
            "端口": opend.port,
            "市场": futu_market,
            "环境": self.config.gateway_env,
        }, self.config.gateway_name)
        if self.config.gateway_connect_wait_seconds > 0:
            time.sleep(float(self.config.gateway_connect_wait_seconds))
        return VnpyExecutor(
            self.repo_root / "state" / "runs",
            mode="live_submit",
            reconciliation_guard=self.reconciliation_guard,
            main_engine=self.main_engine,
            gateway_name=self.config.gateway_name,
            explicit_submit=True,
        )


    def _load_simple_limits(self, path: Path) -> dict[str, float | int | str]:
        limits: dict[str, float | int | str] = {}
        in_limits = False
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line == "limits:":
                in_limits = True
                continue
            if not in_limits or ":" not in line:
                continue
            key, value = line.split(":", 1)
            value = value.strip()
            try:
                number = float(value)
                limits[key.strip()] = int(number) if number.is_integer() else number
            except ValueError:
                limits[key.strip()] = value
        return limits

    def _live_submit_block_reasons(self, live_submit: bool) -> list[str]:

        if not live_submit:
            return []
        reasons: list[str] = []
        if not self.config.live_submit_enabled:
            reasons.append("VNPY_LIVE_CONFIG is not YES")
        if os.environ.get(self.config.env_var_name) != "YES":
            reasons.append(f"{self.config.env_var_name} is not YES")
        if self.config.approval_required and os.environ.get(self.config.approval_env_var_name) != "YES":
            reasons.append(f"{self.config.approval_env_var_name} is not YES")
        if self.config.gateway_env.upper() == "REAL" and os.environ.get("FUTU_TRADE_ENV", "").upper() != "REAL":
            reasons.append("FUTU_TRADE_ENV is not REAL")
        if self.config.gateway_env.upper() == "REAL" and not os.environ.get(self.config.gateway_password_env_var_name):
            reasons.append(f"{self.config.gateway_password_env_var_name} is missing")
        return reasons

    def _get_quote_rows(self, codes: list[str]) -> list[dict[str, Any]]:

        if not codes:
            return []
        return self.quote_client.get_snapshot(codes)

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

    def _build_candidate_pool(self, candidates: list[dict[str, Any]], quote_map: dict[str, dict[str, Any]], account_summary: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for candidate in candidates:
            symbol = candidate["symbol"]
            quote = quote_map.get(symbol)
            if not quote:
                continue
            external_selection = self.external_selection_store.load_latest(self.config.market, symbol)
            evaluation = self.strategy_engine.evaluate_candidate(
                candidate=candidate,
                quote=quote,
                flow_divisor=self.config.flow_divisor,
                has_event_catalyst=self._has_event_catalyst(candidate),
                external_strategy_selection=external_selection,
            )
            strategy_selection = evaluation.metadata.get("strategy_selection", {})
            self.selection_store.append(self.config.market, {"symbol": symbol, "market": self.config.market, "strategy_selection": strategy_selection, "raw_score": evaluation.raw_score, "task_score": evaluation.task_score})
            price = self._normalize_limit_price(float(quote.get("price", quote.get("last_price")) or 0))
            qty = self._calc_qty(price, evaluation.signal.target_position_pct)
            order_value = self._order_value(qty, price)
            risk_context = self.risk_context_builder.build(account_summary, symbol=symbol, order_value=order_value)
            side = "BUY" if evaluation.signal.direction == "long" and evaluation.signal.allow_trade else ""
            gate_result = None
            idem_result = None
            intent = self._build_intent(symbol, side, qty, price, evaluation, risk_context.to_dict()) if side and qty > 0 else None
            if intent:
                live_request = LiveOrderRequest(
                    request_id=intent.request_id,
                    symbol=intent.symbol,
                    market=intent.market,
                    side=intent.side,
                    qty=float(intent.qty),
                    target_position_pct=float(evaluation.signal.target_position_pct),
                    notional=order_value,
                    order_type="LIMIT",
                    price=intent.price,
                    reason=intent.reason,
                    source="live_pipeline",
                    mode="live" if self.live_submit else "paper",
                )
                gate_result = self.live_gate.evaluate(
                    live_request,
                    mode="live" if self.live_submit else "paper",
                    signal_age_seconds=0,
                    market_existing_pct=risk_context.market_exposure_pct,
                    daily_new_pct=risk_context.daily_new_pct,
                    current_drawdown_pct=risk_context.current_drawdown_pct,
                    account_status=account_summary.status,
                    approval_status=self._approval_status(),
                )
                idem_result = self.idempotency_guard.evaluate(intent.request_id)
                if not idem_result.allowed or not gate_result.allowed:
                    intent = None
            rows.append({
                "symbol": symbol,
                "name": candidate.get("name"),
                "price": price,
                "task_score": evaluation.task_score,
                "raw_score": evaluation.raw_score,
                "allow_trade": evaluation.signal.allow_trade,
                "target_position_pct": evaluation.signal.target_position_pct,
                "strategy_selection": strategy_selection,
                "external_strategy_selection": external_selection.to_dict() if external_selection else None,
                "qty": qty,
                "order_value": order_value,
                "order_value_limit": self._order_value_limit(),
                "sizing_status": "ok" if qty > 0 else self._sizing_block_reason(price),
                "risk_context": risk_context.to_dict(),
                "live_gate": gate_result.__dict__ if gate_result else None,
                "idempotency": idem_result.__dict__ if idem_result else None,
                "order_intent": intent,
            })
        return rows

    def _build_intent(self, symbol: str, side: str, qty: int, price: float, evaluation: Any, risk_snapshot: dict[str, Any] | None = None) -> OrderIntent:
        request_id = hashlib.md5(f"{self.config.task_name}|{symbol}|{side}|{datetime.now().strftime('%Y%m%d%H%M')}".encode("utf-8")).hexdigest()[:16]
        return OrderIntent(
            request_id=request_id,
            symbol=symbol,
            market=self.config.market,
            strategy_id=evaluation.signal.strategy_id,
            side=side,
            qty=qty,
            price=price,
            target_position_pct=evaluation.signal.target_position_pct,
            reason=evaluation.signal.reason,
            signal_snapshot={"strategy_selection": evaluation.metadata.get("strategy_selection", {}), "score": evaluation.signal.score},
            risk_snapshot=risk_snapshot or {},
        )

    def _calc_qty(self, price: float, target_position_pct: float) -> int:
        if price <= 0 or target_position_pct <= 0 or not math.isfinite(price):
            return 0
        lot = max(int(self.config.lot_size_default), 1)
        per_lot_value = price * lot
        value_limit = self._order_value_limit()
        if value_limit <= 0 or per_lot_value > value_limit:
            return 0

        target_pct = min(max(float(target_position_pct), 0.0), 1.0)
        desired_value = max(self.config.budget_per_trade * target_pct, per_lot_value)
        desired_value = min(desired_value, value_limit)
        qty = (int(desired_value // price) // lot) * lot
        while qty > 0 and self._order_value(qty, price) > value_limit + 1e-9:
            qty -= lot
        return max(qty, 0)

    def _approval_status(self) -> str:
        if not self.live_submit:
            return "pending"
        if not self.config.approval_required:
            return "approved"
        return "approved" if os.environ.get(self.config.approval_env_var_name) == "YES" else f"pending_env:{self.config.approval_env_var_name}"

    def _order_value_limit(self) -> float:
        limit = max(float(self.config.budget_per_trade), 0.0)
        if self.config.max_order_value is not None:
            limit = min(limit, max(float(self.config.max_order_value), 0.0))
        return limit

    def _order_value(self, qty: int, price: float) -> float:
        if qty <= 0 or price <= 0:
            return 0.0
        return round(float(qty) * float(price), 4)

    def _normalize_limit_price(self, price: float) -> float:
        if price <= 0 or not math.isfinite(price):
            return 0.0
        buffer = max(float(self.config.limit_price_buffer_pct), 0.0)
        return round(price * (1.0 + buffer), 4)

    def _sizing_block_reason(self, price: float) -> str:
        if price <= 0 or not math.isfinite(price):
            return "invalid_price"
        lot = max(int(self.config.lot_size_default), 1)
        if price * lot > self._order_value_limit():
            return "min_lot_exceeds_order_value_limit"
        return "target_size_too_small"

    def _has_event_catalyst(self, candidate: dict[str, Any]) -> bool:
        rationale = candidate.get("rationale") or ""
        return any(keyword in rationale for keyword in self.config.catalyst_keywords)
