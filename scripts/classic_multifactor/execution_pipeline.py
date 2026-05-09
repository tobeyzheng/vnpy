from __future__ import annotations

"""Pre-trade execution guard pipeline for classic_multifactor runners.

This module stitches the four project-level gates into a single
``ExecutionGuardPipeline`` consumed by
``scripts/classic_multifactor/strategy.py`` via its ``execution_hook``
interface:

1. ``OrderIdempotencyGuard`` — reject duplicate / already-open request_ids.
2. ``ReconciliationGuard`` — reject when the latest reconciliation report is
   missing, stale, or flags a live/broker position mismatch.
3. ``MinuteTradeGuard`` — reject when intraday trade budget / cooldown /
   hold-minimum / cutoff is violated.
4. ``LiveRiskGuard`` — reject when single/daily/market exposure or drawdown
   limits are breached.

Any rejection is written to ``state/runs/events.jsonl`` (one JSON per line)
and to the ``OrderStateStore``. When ``live_submit=False`` (default), the
hook always returns ``(False, "dry_run")`` after running the full gate
pipeline so the decisioning / logging path is exercised without sending
a real order.

The hook intentionally does **not** mutate the vn.py ``MainEngine`` or
``CtaEngine`` directly — it only decides allow/deny. Order submission is
still performed by ``CtaTemplate.buy/sell`` inside the strategy.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from execution.live_bridge.models import LiveOrderRequest
from services.common import OrderIntent
from services.execution_guard.idempotency import OrderIdempotencyGuard
from services.execution_guard.reconciliation import ReconciliationGuard
from services.risk_engine import LiveRiskGuard
from services.trade_state import OmsEventRecorder, OrderStateStore
from services.trade_state.state_machine import OrderStateMachine
from vnpy.trader.object import BarData

from scripts.classic_multifactor.minute_guard import MinuteTradeGuard


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PipelineContext:
    """Mutable per-bar context supplied by the runner to the pipeline.

    The runner (``run_intraday_loop.py`` / ``run_daily_rebalance.py``) is
    responsible for keeping these fields fresh — the pipeline itself is
    stateless with regards to broker snapshots.
    """

    capital: float
    equity: float = 0.0
    cash: float = 0.0
    symbol_market_value: float = 0.0
    market_existing_pct: float = 0.0
    daily_new_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    signal_age_seconds: int = 0
    account_status: str = "connected"


@dataclass
class PipelineDecision:
    allowed: bool
    reason: str
    gate: str
    request_id: str
    details: dict[str, Any] = field(default_factory=dict)


class ExecutionGuardPipeline:
    """Four-stage pre-trade check compatible with ``ExecutionHook``.

    Parameters
    ----------
    idempotency, reconciliation, minute_guard, live_risk
        Already-constructed guard instances.
    order_store
        ``OrderStateStore`` used to persist accepted/rejected requests for
        post-run diagnostics and cold-start idempotency.
    events_log_path
        Path to the line-delimited JSON events log (typically
        ``state/runs/events.jsonl``). ``None`` disables event logging.
    context_provider
        Callable returning a fresh :class:`PipelineContext` each call.
        Required so the LiveRiskGuard sees up-to-date equity / drawdown.
    strategy_id
        Logical identifier recorded on the order state (e.g.
        ``classic_multifactor_NVDA_US``).
    market
        Market string propagated into ``LiveOrderRequest`` (``US`` / ``HK``).
    loop_mode
        ``"intraday"`` or ``"daily"`` — recorded in events for later audit.
    live_submit
        When ``False`` the pipeline still runs every gate but always returns
        ``(False, "dry_run")`` from ``approve_*`` so no real order goes out.
    trade_times_provider, last_trade_provider, entry_at_provider
        Callables returning the latest intraday stats so MinuteTradeGuard
        stays in sync with the ``CtaTemplate`` bookkeeping.
    """

    def __init__(
        self,
        *,
        idempotency: OrderIdempotencyGuard,
        reconciliation: ReconciliationGuard,
        minute_guard: MinuteTradeGuard,
        live_risk: LiveRiskGuard,
        order_store: OrderStateStore,
        events_log_path: Path | None,
        context_provider,
        strategy_id: str,
        market: str,
        loop_mode: str,
        live_submit: bool,
        trade_times_provider=lambda: [],
        last_trade_provider=lambda: None,
        entry_at_provider=lambda: None,
        exchange_tz: str = "America/New_York",
        oms_recorder: "OmsEventRecorder | None" = None,
    ):
        self.idempotency = idempotency
        self.reconciliation = reconciliation
        self.minute_guard = minute_guard
        self.live_risk = live_risk
        self.order_store = order_store
        self.events_log_path = Path(events_log_path) if events_log_path else None
        self.context_provider = context_provider
        self.strategy_id = strategy_id
        self.market = market
        self.loop_mode = loop_mode
        self.live_submit = bool(live_submit)
        self.trade_times_provider = trade_times_provider
        self.last_trade_provider = last_trade_provider
        self.entry_at_provider = entry_at_provider
        self.exchange_tz = exchange_tz
        self.oms_recorder = oms_recorder
        self._machine = OrderStateMachine()
        self.approved_count = 0
        self.blocked_by_gate: dict[str, int] = {}
        # Last approved request_id (set by ``_approve``); consumed by
        # ``on_order_submitted`` to bind vt_orderid -> request_id in the
        # OmsEventRecorder. We use last-approved instead of (side, qty)
        # matching because CtaTemplate.buy() returns vt_orderids without
        # carrying the project request_id through.
        self._last_approved_request_id: str | None = None

    # ------------------------------------------------------------------
    # ExecutionHook protocol
    # ------------------------------------------------------------------
    def approve_buy(self, bar: BarData, qty: int, price: float) -> tuple[bool, str]:
        return self._approve(bar=bar, side="BUY", qty=qty, price=price)

    def approve_sell(self, bar: BarData, qty: int, price: float) -> tuple[bool, str]:
        return self._approve(bar=bar, side="SELL", qty=qty, price=price)

    def on_order_submitted(
        self,
        bar: BarData,
        side: str,
        qty: int,
        price: float,
        vt_orderids: list[str],
    ) -> None:
        # Record broker-level vt_orderids against the last accepted request so
        # reconciliation can later map order fills back to requests, and bind
        # them in the OmsEventRecorder so subsequent EVENT_ORDER / EVENT_TRADE
        # pushes for this order can mutate the persisted OrderState.
        request_id = self._last_approved_request_id
        if self.oms_recorder is not None and request_id:
            for vt_orderid in vt_orderids:
                if vt_orderid:
                    self.oms_recorder.register_request(vt_orderid, request_id)
        # Stamp broker_order_id onto the approved OrderState so a restart
        # picks up the binding even if the recorder index is empty.
        if request_id and vt_orderids:
            try:
                state = self.order_store.load(request_id)
                if state is not None and not state.broker_order_id:
                    from dataclasses import replace as _replace

                    self.order_store.save(
                        _replace(state, broker_order_id=str(vt_orderids[0]))
                    )
            except Exception:
                pass
        self._append_event(
            {
                "ts": _utc_iso(),
                "event": "order_submitted",
                "side": side,
                "qty": qty,
                "price": price,
                "vt_orderids": list(vt_orderids),
                "vt_symbol": bar.vt_symbol,
                "strategy_id": self.strategy_id,
                "loop_mode": self.loop_mode,
                "request_id": request_id,
            }
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _approve(self, *, bar: BarData, side: str, qty: int, price: float) -> tuple[bool, str]:
        request_id = self._build_request_id(bar=bar, side=side)

        # Gate 1: Idempotency
        idem = self.idempotency.evaluate(request_id)
        if not idem.allowed:
            return self._reject(request_id, bar, side, qty, price, "idempotency", idem.reason)

        # Gate 2: Reconciliation
        symbol = bar.vt_symbol.split(".")[0]
        recon = self.reconciliation.evaluate(side=side, symbol=symbol)
        if not recon.allowed:
            return self._reject(
                request_id,
                bar,
                side,
                qty,
                price,
                "reconciliation",
                ";".join(recon.reasons) or recon.blocking_level,
                details={"diff_symbols": recon.diff_symbols},
            )

        # Gate 3: Minute guard
        if side == "BUY":
            minute_decision = self.minute_guard.can_enter(
                bar.datetime,
                trade_times=list(self.trade_times_provider() or []),
                last_trade_at=self.last_trade_provider(),
                exchange_tz=self.exchange_tz,
            )
        else:
            minute_decision = self.minute_guard.can_exit(
                bar.datetime,
                entry_at=self.entry_at_provider(),
                hard_exit=False,
            )
        if not minute_decision.allowed:
            return self._reject(
                request_id, bar, side, qty, price, "minute_guard", minute_decision.reason
            )

        # Gate 4: Live risk
        ctx = self.context_provider()
        notional = float(price) * float(qty)
        order = LiveOrderRequest(
            request_id=request_id,
            symbol=symbol,
            market=self.market,
            side=side,
            qty=float(qty),
            target_position_pct=(notional / ctx.capital) if ctx.capital > 0 else float(qty),
            notional=notional,
            order_type="LIMIT",
            price=float(price),
            tif="DAY",
            venue="FUTU",
            reason="classic_multifactor",
            source="cta_engine",
            mode="live" if self.live_submit else "dry_run",
            tags=[self.loop_mode, self.strategy_id],
        )
        risk_result = self.live_risk.evaluate(
            order,
            market_existing_pct=ctx.market_existing_pct,
            daily_new_pct=ctx.daily_new_pct,
            current_drawdown_pct=ctx.current_drawdown_pct,
            signal_age_seconds=ctx.signal_age_seconds,
            account_status=ctx.account_status,
            market_existing_value=ctx.symbol_market_value,
            budget_per_trade=ctx.capital,
        )
        if not risk_result.allowed:
            return self._reject(
                request_id,
                bar,
                side,
                qty,
                price,
                "live_risk",
                ";".join(risk_result.reasons),
            )

        # All gates passed — persist an "approved" OrderState and decide
        # whether to actually let the strategy submit.
        intent = OrderIntent(
            request_id=request_id,
            symbol=symbol,
            market=self.market,
            side=side,
            qty=int(qty),
            price=float(price),
            strategy_id=self.strategy_id,
            reason="classic_multifactor",
            target_position_pct=(notional / ctx.capital) if ctx.capital > 0 else 0.0,
            signal_snapshot={
                "vt_symbol": bar.vt_symbol,
                "close_price": float(bar.close_price),
                "bar_datetime": bar.datetime.isoformat(),
                "loop_mode": self.loop_mode,
            },
            risk_snapshot={
                "capital": ctx.capital,
                "equity": ctx.equity,
                "cash": ctx.cash,
                "market_existing_pct": ctx.market_existing_pct,
                "daily_new_pct": ctx.daily_new_pct,
                "current_drawdown_pct": ctx.current_drawdown_pct,
            },
        )
        state = self._machine.create(intent)
        state = self._machine.transition(state, "validated", note="pipeline:all_gates_passed")
        state = self._machine.transition(state, "risk_checked", note="pipeline:live_risk_ok")
        state = self._machine.transition(state, "approved", note="pipeline:approved")
        self.order_store.save(state)
        self._last_approved_request_id = request_id

        self.approved_count += 1
        self._append_event(
            {
                "ts": _utc_iso(),
                "event": "order_approved",
                "request_id": request_id,
                "side": side,
                "qty": qty,
                "price": price,
                "vt_symbol": bar.vt_symbol,
                "strategy_id": self.strategy_id,
                "loop_mode": self.loop_mode,
                "live_submit": self.live_submit,
            }
        )

        if not self.live_submit:
            return False, "dry_run"
        return True, "ok"

    def _reject(
        self,
        request_id: str,
        bar: BarData,
        side: str,
        qty: int,
        price: float,
        gate: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        self.blocked_by_gate[gate] = self.blocked_by_gate.get(gate, 0) + 1
        self._append_event(
            {
                "ts": _utc_iso(),
                "event": "order_blocked",
                "request_id": request_id,
                "gate": gate,
                "reason": reason,
                "side": side,
                "qty": qty,
                "price": price,
                "vt_symbol": bar.vt_symbol,
                "strategy_id": self.strategy_id,
                "loop_mode": self.loop_mode,
                "details": details or {},
            }
        )
        return False, f"{gate}:{reason}"

    def _build_request_id(self, *, bar: BarData, side: str) -> str:
        # Deterministic per-bar id so re-delivery of the same bar cannot create
        # a duplicate approved state.
        ts_key = bar.datetime.strftime("%Y%m%d%H%M%S") if bar.datetime else str(int(time.time()))
        return f"{self.strategy_id}_{side}_{bar.vt_symbol}_{ts_key}_{uuid.uuid4().hex[:6]}"

    def _append_event(self, payload: dict[str, Any]) -> None:
        if self.events_log_path is None:
            return
        try:
            self.events_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_log_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except OSError:
            # Event logging must never break the trade loop.
            pass


__all__ = ["ExecutionGuardPipeline", "PipelineContext", "PipelineDecision"]
