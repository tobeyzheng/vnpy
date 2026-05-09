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
from services.execution_guard.live_gate import LiveExecutionGate, LiveGateResult
from services.execution_guard.precheck import SubmitPrecheck
from services.execution_guard.reconciliation import ReconciliationGuard
from services.futu_account import FutuAccountProvider, FutuQuoteClient
from services.futu_opend import OpenDConfig
from services.risk_engine import LiveRiskGuard

from services.strategy.candidate_provider import UnifiedCandidateProvider
from services.strategy.classic_adapter import (
    HARD_EXIT_REASONS,
    ClassicDecision,
    ClassicSignalAdapter,
)
from services.strategy.engine import StrategyEngine
from services.strategy.external_selection import ExternalStrategySelectionStore
from services.strategy.selection_store import StrategySelectionStore
from services.trade_state import OrderStateStore
from services.trade_state.strategy_state import StrategyStateStore
from scripts.classic_multifactor.minute_guard import MinuteGuardDecision, MinuteTradeGuard, MinuteTradeGuardConfig  # noqa: F401



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
    order_finalize_timeout_seconds: float = 30.0
    no_new_entry_timezone: str = "America/New_York"
    # live-strict account selection (Plan C)
    live_account_strict: bool = False
    expected_acc_type: str = "MARGIN"
    expected_last4_env_var: str = "FUTU_ACCOUNT_LAST4"



class LiveTradingPipeline:
    def __init__(self, repo_root: Path, config: LiveTaskConfig, *, live_submit: bool = False):
        self.repo_root = repo_root
        self.config = config
        self.live_submit_requested = bool(live_submit)
        self.live_submit_block_reasons = self._live_submit_block_reasons(live_submit)
        self.live_submit = bool(live_submit and not self.live_submit_block_reasons)
        self.report_path = repo_root / "state" / "runs" / config.report_filename

        self.quote_client = FutuQuoteClient()
        self.account_provider = self._build_account_provider()
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

        # 创建minute_guard实例
        # minute_guard 的默认阈值来自 live_risk_limits.yaml；candidate 自身若带
        # strategy_config（来自 nvda_g09.json 等策略 JSON）会在 _candidate_guard()
        # 里按 candidate 维度再覆盖一次，确保研究参数真实落地到实盘。
        self.default_minute_guard_config = MinuteTradeGuardConfig.from_setting({
            "max_intraday_trades": limits.get("max_intraday_trades", 4),
            "entry_cooldown_minutes": limits.get("entry_cooldown_minutes", 30),
            "min_hold_minutes": limits.get("min_hold_minutes", 20),
            "no_new_entry_after": limits.get("no_new_entry_after", "15:30"),
        })
        self.minute_guard = MinuteTradeGuard(self.default_minute_guard_config)

        self.reconciliation_guard = ReconciliationGuard(
            repo_root / "state" / "runs" / config.reconciliation_filename,
            max_age_minutes=config.reconciliation_max_age_minutes,
            fail_closed=True,
            cold_start_sentinel=repo_root / "state" / "runs" / ".reconciliation_ever_generated",

        ) if config.reconciliation_required else None
        self.event_engine: EventEngine | None = None
        self.main_engine: MainEngine | None = None
        self.event_recorder: VnpyEventRecorder | None = None
        self.gateway_event_bridge: VnpyGatewayEventBridge | None = None

        # Strategy-level persistent state (entry_at / entry_price / highest_close /
        # last_trade_at) survives 5-minute loop restarts. See
        # ``services/trade_state/strategy_state.py`` and the audit requirements
        # 2.3 / 3.x for details.
        self.strategy_state_store = StrategyStateStore(repo_root, config.task_name)

        # Classic multifactor adapter: bridges ``ClassicMultiFactorModel.decide_target``
        # into the live scoring chain so "backtest decision == live decision".
        self.classic_adapter = ClassicSignalAdapter(bar_loader=self._load_classic_bars)

    def _build_account_provider(self) -> FutuAccountProvider:
        """Build FutuAccountProvider with strict live-account selection when configured.

        Identity check is fully delegated to OpenD-reported trd_env + acc_type + market;
        FUTU_ACCOUNT_LAST4 is no longer consulted here.
        """
        if not self.config.live_account_strict:
            return FutuAccountProvider()
        expect_market = "US" if self.config.market == "us" else "HK" if self.config.market == "hong_kong" else ""
        return FutuAccountProvider(
            live_strict=True,
            expect_trd_env=(self.config.gateway_env or "").upper() or "REAL",
            expect_acc_type=(self.config.expected_acc_type or "").upper() or None,
            expect_market=expect_market or None,
            expect_last4=None,
        )

    def _sync_today_trades(self, symbol: str) -> list[datetime]:
        """同步 Futu 账户今日真实成交记录（按标的过滤）。

        Returns: 升序的 datetime 列表。任何异常都向上抛出，由调用方
        决定是降级放行还是 fail-closed。
        """
        today_trades = self.account_provider.get_today_trades(symbol)
        print(f"[sync_today_trades] symbol={symbol} 同步到 {len(today_trades)} 笔今日成交", flush=True)
        for i, trade_time in enumerate(today_trades[:5]):
            print(f"  [{i+1}] {trade_time.strftime('%H:%M:%S')}", flush=True)
        if len(today_trades) > 5:
            print(f"  ... 还有 {len(today_trades) - 5} 笔成交记录", flush=True)
        return today_trades

    def _sync_today_trade_details(self, symbol: str) -> list[dict]:
        """Detailed deal rows for ``symbol`` (price/qty/side/notional/time).

        Used by the live risk context to derive today's BUY notional directly
        from the broker's own deal flow. Any exception is re-raised so the
        caller can fail-closed (requirement 7.4).
        """
        return self.account_provider.get_today_trade_details(symbol)

    def run(self) -> dict[str, Any]:
        # Plan C: live-strict account selection — abort before anything else if mismatch.
        account_summary = self.account_provider.get_summary()
        if account_summary.status == "live_account_mismatch":
            self.live_submit = False
            if "live account mismatch" not in ",".join(self.live_submit_block_reasons):
                self.live_submit_block_reasons = list(self.live_submit_block_reasons) + [
                    f"live account mismatch: {account_summary.message}"
                ]
            report = {
                "task": self.config.task_name,
                "market": self.config.market,
                "live_submit_requested": self.live_submit_requested,
                "live_submit_effective": False,
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
                    "live_account_strict": self.config.live_account_strict,
                    "expected_acc_type": self.config.expected_acc_type,
                    "expected_last4": None,
                    "expected_market": "US" if self.config.market == "us" else "HK" if self.config.market == "hong_kong" else "",
                },
                "account_status": account_summary.status,
                "account_message": account_summary.message,
                "selected": [],
                "order_states": [],
            }
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return report

        candidates = UnifiedCandidateProvider(self.repo_root).load(self.config.market)[: max(self.config.max_candidates, 0)]
        codes = [c["symbol"] for c in candidates]
        quote_rows = self._get_quote_rows(codes)
        quote_map = self._build_symbol_map(quote_rows, "code")
        pool = self._build_candidate_pool(candidates, quote_map, account_summary)
        pool.sort(key=lambda row: row["task_score"], reverse=True)
        selected = pool[: max(self.config.max_selected, 0)]

        executor = self._build_executor() if self.live_submit else VnpyExecutor(self.repo_root / "state" / "runs", mode="paper")
        states = [executor.execute_intent(row["order_intent"]) for row in selected if row.get("order_intent")]
        if self.live_submit and states:
            # Wait for broker-side order status to reach terminal (filled/cancelled/rejected)
            # or timeout before tearing down MainEngine; otherwise EVENT_TRADE回报会丢失，
            # 订单状态长期停留在 'submitted'，跨进程风控/minute_guard 会错判。
            states = self._wait_for_order_states(states)

        # Persist strategy-level state on filled BUY / SELL so the next loop
        # iteration reuses entry_at / entry_price / highest_close rather than
        # resetting to zero. See requirements 2.3 / 3.1 / 3.3.
        self._persist_strategy_state_from_states(states)
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
        self._executor_state_store = self.order_store  # alias used by _wait_for_order_states
        return VnpyExecutor(
            self.repo_root / "state" / "runs",
            mode="live_submit",
            reconciliation_guard=self.reconciliation_guard,
            main_engine=self.main_engine,
            gateway_name=self.config.gateway_name,
            explicit_submit=True,
        )


    def _wait_for_order_states(self, states: list[Any]) -> list[Any]:
        """Poll OrderStateStore until every order reaches a terminal status, or timeout.

        Terminal statuses: filled / cancelled / rejected / failed / reconciled / expired.
        This is what makes EVENT_TRADE/EVENT_ORDER回报 actually get reflected in
        the report and the local state store before MainEngine.close() is called.
        """
        terminal = {"filled", "cancelled", "rejected", "failed", "reconciled", "expired"}
        timeout = max(float(self.config.order_finalize_timeout_seconds or 0.0), 0.0)
        deadline = time.time() + timeout if timeout > 0 else None
        ids = [s.request_id for s in states if getattr(s, "request_id", None)]
        refreshed: dict[str, Any] = {s.request_id: s for s in states}
        while deadline is None or time.time() < deadline:
            all_done = True
            for rid in ids:
                latest = self.order_store.load(rid)
                if latest is not None:
                    refreshed[rid] = latest
                status = getattr(refreshed[rid], "status", "")
                if status not in terminal:
                    all_done = False
            if all_done:
                break
            time.sleep(0.5)
        return [refreshed[s.request_id] for s in states]

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
        # NOTE: FUTU_ACCOUNT_LAST4 env precheck removed on purpose.
        # Live-account identity is enforced by FutuAccountProvider(live_strict=True)
        # using OpenD-reported trd_env + acc_type + market; run() will abort on
        # account_summary.status == "live_account_mismatch".
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

    def _candidate_guard(self, candidate: dict[str, Any]) -> MinuteTradeGuard:
        """Build a per-candidate MinuteTradeGuard, overriding defaults with
        strategy_config fields carried on the candidate (e.g. nvda_g09.json).
        """
        cfg = self.default_minute_guard_config
        strategy_cfg = candidate.get("strategy_config") or {}
        if not isinstance(strategy_cfg, dict):
            return MinuteTradeGuard(cfg)

        # Defaults come from live_risk_limits.yaml; any field present on the
        # candidate's strategy_config wins. Goes through the shared factory so
        # new fields only need to be declared in one place.
        merged = {
            "max_intraday_trades": cfg.max_intraday_trades,
            "entry_cooldown_minutes": cfg.entry_cooldown_minutes,
            "min_hold_minutes": cfg.min_hold_minutes,
            "no_new_entry_after": cfg.no_new_entry_after,
        }
        for key in merged:
            if strategy_cfg.get(key) is not None:
                merged[key] = strategy_cfg[key]
        override = MinuteTradeGuardConfig.from_setting(merged)
        return MinuteTradeGuard(override)

    def _guard_now(self) -> datetime:
        """Return the 'now' timestamp that MinuteTradeGuard should use for its
        no_new_entry_after cutoff. For US live trading this has to be the
        exchange local time (America/New_York), not Beijing local time.

        Unlike the previous implementation we keep ``tzinfo`` attached so
        downstream guards can also normalise ``trade_times`` to the same
        exchange timezone when counting ``max_intraday_trades``.
        """
        try:
            from zoneinfo import ZoneInfo
            tz_name = self.config.no_new_entry_timezone or "America/New_York"
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            return datetime.now()

    def _guard_exchange_tz(self) -> str:
        """Exchange timezone string passed into MinuteTradeGuard.can_enter."""
        return self.config.no_new_entry_timezone or "America/New_York"

    # ------------------------------------------------------------------
    # Classic multifactor adapter plumbing
    # ------------------------------------------------------------------
    def _load_classic_bars(self, vt_symbol: str, start: datetime, end: datetime) -> list[Any]:
        """Bar loader injected into ``ClassicSignalAdapter``.

        Uses ``VnpyBarRepository`` so live and backtest share the same DB
        tables. ``fetch_futu_history=False`` because the live path is
        time-sensitive and should rely on the already-synced local DB.
        """
        try:
            from scripts.classic_multifactor.data import VnpyBarRepository
        except Exception as exc:
            print(f"[classic_adapter] VnpyBarRepository import failed: {exc}", flush=True)
            return []
        try:
            repo = VnpyBarRepository(fetch_futu_history=False)
            _vt, _futu, bars = repo.load_us_bars(vt_symbol, start, end, "1m")
            return bars or []
        except Exception as exc:
            print(f"[classic_adapter] load_us_bars failed for {vt_symbol}: {exc}", flush=True)
            return []

    def _position_qty_for_symbol(self, account_summary: Any, symbol: str) -> tuple[int, float]:
        """Return (qty, cost_basis) for ``symbol`` from Futu account snapshot.

        Symbols are compared by their bare ticker (``NVDA`` from ``NVDA.US``
        or ``US.NVDA``) to be resilient against gateway format differences.
        """
        target = symbol.replace("US.", "").replace(".US", "").upper()
        positions = getattr(account_summary, "positions", None) or []
        for pos in positions:
            raw_code = str(getattr(pos, "code", "")).upper()
            bare = raw_code.replace("US.", "").replace(".US", "")
            if bare == target:
                qty = int(float(getattr(pos, "qty", 0) or 0))
                # FutuPosition does not carry cost basis in the current
                # snapshot; callers should only trust this as a sign of
                # "we have a position" rather than an exact entry price.
                return qty, 0.0
        return 0, 0.0

    def _reconcile_strategy_state(
        self,
        symbol: str,
        *,
        account_summary: Any,
        trades: list[datetime],
    ) -> Any:
        """Pull the persisted StrategyState and reconcile with broker data."""
        now = self._guard_now()
        # Roll intraday counters forward when a new exchange-local day starts.
        self.strategy_state_store.rollover_if_new_day(symbol, now.date())
        qty, _cost = self._position_qty_for_symbol(account_summary, symbol)
        last_trade = max(trades) if trades else None
        # ``qty`` is all we reliably get from FutuAccountSummary; treat
        # cost_basis=None so reconciliation keeps local entry_price unless
        # explicitly provided by a richer snapshot.
        return self.strategy_state_store.reconcile_with_futu(
            symbol,
            futu_entry_price=None,
            futu_qty=float(qty) if qty else None,
            futu_last_trade_at=last_trade,
            now=now,
        )

    def _evaluate_with_classic_adapter(
        self,
        *,
        candidate: dict[str, Any],
        quote: dict[str, Any],
        current_qty: int,
        state: Any,
        account_summary: Any,
    ) -> tuple[Any, ClassicDecision] | None:
        """Return ``(StrategyEvaluation, ClassicDecision)`` if the candidate is
        handled by the classic adapter, ``None`` to fall back to the generic
        StrategyEngine path.
        """
        if not ClassicSignalAdapter.is_classic_candidate(candidate):
            return None
        equity = float(getattr(account_summary, "total_assets", 0) or 0)
        cash = float(getattr(account_summary, "cash", 0) or 0)
        classic = self.classic_adapter.evaluate(
            candidate=candidate,
            quote=quote,
            current_qty=int(current_qty or 0),
            entry_price=float(getattr(state, "entry_price", 0.0) or 0.0),
            highest_close=float(getattr(state, "highest_close", 0.0) or 0.0),
            equity=equity,
            cash=cash,
            now=self._guard_now(),
        )
        evaluation = self.classic_adapter.to_evaluation(candidate=candidate, classic=classic)
        return evaluation, classic

    def _persist_strategy_state_from_states(self, states: list[Any]) -> None:
        """After the loop's orders settle, update ``StrategyStateStore`` based
        on terminal order states so entry_at / entry_price survive restarts.
        """
        if not states:
            return
        now = self._guard_now()
        for state in states:
            status = str(getattr(state, "status", "") or "").lower()
            side = str(getattr(state, "side", "") or "").upper()
            symbol = str(getattr(state, "symbol", "") or "")
            filled_qty = int(float(getattr(state, "filled_qty", 0) or 0))
            avg_fill_price = float(getattr(state, "avg_fill_price", 0) or 0)
            if not symbol or status not in {"filled", "partial_filled"}:
                continue
            if filled_qty <= 0:
                continue
            try:
                if side == "BUY":
                    self.strategy_state_store.update_on_buy(
                        symbol,
                        price=avg_fill_price or float(getattr(state, "price", 0) or 0),
                        at=now,
                        last_signal=str(getattr(state, "reason", "") or "classic_multifactor_entry"),
                    )
                elif side == "SELL":
                    # Full exit -> clear; partial exit leaves state intact so
                    # ATR trailing / highest_close keep working for remainder.
                    target_qty = int(float(getattr(state, "qty", 0) or 0))
                    if filled_qty >= target_qty and target_qty > 0:
                        self.strategy_state_store.clear_on_sell(symbol, at=now)
            except Exception as exc:
                print(f"[strategy_state] persist failed symbol={symbol} side={side}: {exc}", flush=True)

    def _build_candidate_pool(self, candidates: list[dict[str, Any]], quote_map: dict[str, dict[str, Any]], account_summary: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        # 逐 candidate 同步今日成交，避免单次查询被第一个标的绑定；失败直接 fail-closed
        # 返回空集合，由 minute_guard 把后续决策拒绝掉（配合外层的异常处理）。
        trades_by_symbol: dict[str, list[datetime]] = {}
        trade_details_by_symbol: dict[str, list[dict]] = {}
        buy_notional_by_symbol: dict[str, float] = {}
        trades_sync_error: dict[str, str] = {}
        for candidate in candidates:
            sym = candidate.get("symbol") or ""
            if not sym or sym in trades_by_symbol:
                continue
            try:
                details = self._sync_today_trade_details(sym)
                trade_details_by_symbol[sym] = details
                trades_by_symbol[sym] = [row["datetime"] for row in details]
                buy_notional_by_symbol[sym] = sum(
                    float(row.get("notional") or 0.0)
                    for row in details
                    if str(row.get("side", "")).upper() == "BUY"
                )
            except Exception as exc:
                # 获取不到真实成交数时必须 fail-closed，避免像旧实现那样吞异常后
                # 直接放行 minute_guard（曾导致 max_intraday_trades 失效）。
                trades_by_symbol[sym] = []
                trade_details_by_symbol[sym] = []
                buy_notional_by_symbol[sym] = 0.0
                trades_sync_error[sym] = f"{type(exc).__name__}: {exc}"
                print(f"[sync_today_trades] symbol={sym} 失败，将强制拒绝下单: {exc}", flush=True)

        for candidate in candidates:
            symbol = candidate["symbol"]
            quote = quote_map.get(symbol)
            if not quote:
                continue

            # Strategy-level persistent state & account reconciliation
            # --------------------------------------------------------
            # ``current_qty`` from Futu is the single source of truth for
            # whether we hold the symbol right now; local ``entry_price`` /
            # ``highest_close`` survive 5-minute loop restarts via
            # StrategyStateStore so ATR stop-loss / trailing-stop work across
            # process boundaries.
            today_trades_for_state = trades_by_symbol.get(symbol, [])
            current_qty_snapshot, _ = self._position_qty_for_symbol(account_summary, symbol)
            state = self._reconcile_strategy_state(
                symbol,
                account_summary=account_summary,
                trades=today_trades_for_state,
            )

            external_selection = self.external_selection_store.load_latest(self.config.market, symbol)

            # Try the classic adapter first so "backtest decision == live decision".
            # Falls back to the generic StrategyEngine path for non-classic
            # candidates (e.g. hand-picked watchlists without strategy_config).
            classic_payload = self._evaluate_with_classic_adapter(
                candidate=candidate,
                quote=quote,
                current_qty=current_qty_snapshot,
                state=state,
                account_summary=account_summary,
            )
            classic_decision: ClassicDecision | None = None
            if classic_payload is not None:
                evaluation, classic_decision = classic_payload
            else:
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
            # Determine side/qty. Classic adapter can request an ATR-based SELL
            # exit even when there is no fresh entry signal; in that case qty
            # is the current position and ``hard_exit`` bypasses
            # ``min_hold_minutes`` (but NOT live_gate / idempotency / today_trades).
            side = ""
            qty = 0
            sell_exit = False
            if classic_decision is not None and classic_decision.side == "SELL" and current_qty_snapshot > 0:
                side = "SELL"
                qty = int(current_qty_snapshot)
                sell_exit = True
            elif evaluation.signal.direction == "long" and evaluation.signal.allow_trade:
                qty = self._calc_qty(price, evaluation.signal.target_position_pct)
                side = "BUY" if qty > 0 else ""

            order_value = self._order_value(qty, price)
            risk_context = self.risk_context_builder.build(
                account_summary,
                symbol=symbol,
                order_value=order_value,
                today_buy_notional=buy_notional_by_symbol.get(symbol),
            )

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

                # 按 candidate 构造 minute_guard，让 nvda_g09.json 等策略 JSON 里的
                # max_intraday_trades/entry_cooldown/min_hold/no_new_entry_after 真实生效。
                today_trades = trades_by_symbol.get(symbol, [])
                last_trade_at = max(today_trades) if today_trades else None
                now_for_guard = self._guard_now()
                symbol_guard = self._candidate_guard(candidate)
                if symbol in trades_sync_error:
                    minute_guard_result = MinuteGuardDecision(
                        allowed=False,
                        reason=f"today_trades_sync_failed:{trades_sync_error[symbol]}",
                    )
                elif sell_exit:
                    # SELL exits never count towards ``max_intraday_trades`` on the
                    # entry side; only enforce ``min_hold_minutes`` via can_exit,
                    # with hard_exit=True for ATR / stop_loss reasons.
                    entry_at_dt: datetime | None = None
                    if state and state.entry_at:
                        try:
                            entry_at_dt = datetime.fromisoformat(state.entry_at)
                        except Exception:
                            entry_at_dt = None
                    minute_guard_result = symbol_guard.can_exit(
                        now_for_guard,
                        entry_at=entry_at_dt,
                        hard_exit=bool(classic_decision and classic_decision.hard_exit),
                    )
                else:
                    minute_guard_result = symbol_guard.can_enter(
                        now_for_guard,
                        trade_times=today_trades,
                        last_trade_at=last_trade_at,
                        exchange_tz=self._guard_exchange_tz(),
                    )

                if not minute_guard_result.allowed:
                    # minute_guard检查失败，不允许交易
                    gate_result = LiveGateResult(allowed=False, reasons=[f"minute_guard:{minute_guard_result.reason}"])
                else:
                    # minute_guard检查通过，继续其他检查
                    gate_result = self.live_gate.evaluate(
                        live_request,
                        mode="live" if self.live_submit else "paper",
                        signal_age_seconds=0,
                        market_existing_pct=risk_context.market_exposure_pct,
                        daily_new_pct=risk_context.daily_new_pct,
                        current_drawdown_pct=risk_context.current_drawdown_pct,
                        account_status=account_summary.status,
                        approval_status=self._approval_status(),
                        market_existing_value=risk_context.symbol_existing_value,
                        budget_per_trade=float(self.config.budget_per_trade or 0.0),
                    )

                idem_result = self.idempotency_guard.evaluate(intent.request_id)
                if not idem_result.allowed or not gate_result.allowed:
                    intent = None

            today_trades_for_symbol = trades_by_symbol.get(symbol, [])
            exit_reason = classic_decision.exit_reason if classic_decision else None
            today_buy_notional = float(buy_notional_by_symbol.get(symbol, 0.0) or 0.0)
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
                "today_trades_count": len(today_trades_for_symbol),
                "today_buy_notional": round(today_buy_notional, 4),
                "today_trades_sync_error": trades_sync_error.get(symbol),
                "current_qty": int(current_qty_snapshot),
                "entry_price": float(getattr(state, "entry_price", 0.0) or 0.0),
                "highest_close": float(getattr(state, "highest_close", 0.0) or 0.0),
                "exit_reason": exit_reason,
                "classic_decision": classic_decision.to_dict() if classic_decision else None,
                "minute_guard_result": (
                    asdict(minute_guard_result) if side and qty > 0 and hasattr(minute_guard_result, "__dataclass_fields__") else None
                ),
            })

        return rows

    def _build_intent(self, symbol: str, side: str, qty: int, price: float, evaluation: Any, risk_snapshot: dict[str, Any] | None = None) -> OrderIntent:
        # Exchange-local date keeps two orders on the same trading day in the
        # same idempotency bucket, while per-signal seed (strategy_signal_id)
        # lets the strategy emit genuinely independent re-entries later in
        # the day. Minute precision was dropped because loop iterations on a
        # 5-minute cadence generated a fresh request_id even for the same
        # signal, which defeated the purpose of the guard entirely.
        exchange_date = self._guard_now().strftime("%Y%m%d")
        classic_decision = (evaluation.metadata.get("classic_decision") or {}) if hasattr(evaluation, "metadata") else {}
        factor = classic_decision.get("factor") or {}
        strategy_signal_id = str(
            factor.get("datetime")
            or classic_decision.get("reason")
            or evaluation.metadata.get("entry_action", "")
            or "nosig"
        )
        seed = f"{self.config.task_name}|{symbol}|{side}|{exchange_date}|{strategy_signal_id}"
        request_id = hashlib.md5(seed.encode("utf-8")).hexdigest()[:16]
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
            signal_snapshot={
                "strategy_selection": evaluation.metadata.get("strategy_selection", {}),
                "score": evaluation.signal.score,
                "strategy_signal_id": strategy_signal_id,
                "exchange_date": exchange_date,
            },
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
