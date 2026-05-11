from __future__ import annotations

"""OmsEngine event recorder for OrderStateStore (Task 6 / S4).

This module bridges vn.py's ``EVENT_ORDER`` / ``EVENT_TRADE`` pushes into
:class:`services.trade_state.OrderStateStore` as a read-only snapshot
recorder. It does **not** drive the project-side state machine for the
"created -> validated -> approved -> ... -> approved" pre-trade phases —
those transitions are still owned by
``scripts/classic_multifactor/execution_pipeline.ExecutionGuardPipeline``.

Once an order has been approved and submitted, the recorder takes over for
all post-submission status changes by mapping broker pushes back to the
project's ``request_id`` via the ``vt_orderid -> request_id`` index.

Design notes
------------
* **Single source of truth for terminal status**: only EVENT_ORDER /
  EVENT_TRADE pushes can move an OrderState into ``submitted`` /
  ``partial_filled`` / ``filled`` / ``cancelled`` / ``rejected``. The
  pre-trade pipeline stops at ``approved``.
* **Deduplication (DA)**: vn.py replays the latest known order/trade on
  reconnect, and OpenD can also re-deliver pushes after socket hiccups.
  We dedupe by ``vt_tradeid`` for trades and by ``(vt_orderid, status,
  traded)`` for orders so ``filled_qty`` cannot be double-counted.
* **Restart recovery**: ``OrderStateStore.list()`` already round-trips
  every ``OrderState`` from disk. We rebuild the
  ``vt_orderid -> request_id`` map from the persisted
  ``broker_order_id`` field so a restart in the middle of a session
  picks up where we left off.
* **Out-of-order tolerance**: if EVENT_TRADE arrives before
  EVENT_ORDER (rare but observed on Futu reconnect), we apply the trade
  fill to the existing approved/submitted OrderState; the later
  EVENT_ORDER push is harmless because (a) the state machine forbids
  going backwards from ``filled`` to ``submitted``, and (b) we use a
  conservative no-op transition on duplicates.
* The recorder is intentionally tolerant: any ``InvalidOrderTransition``
  or ``KeyError`` on unknown vt_orderid is swallowed and counted in
  :attr:`OmsEventRecorder.stats` so an OmsEngine event glitch can never
  bring down the trading loop.
"""

import json
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from services.common import OrderState

from .state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidOrderTransition,
    OrderStateMachine,
)
from .storage import OrderStateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Statuses that already represent a terminal or near-terminal post-fill state;
# additional pushes for these orders are recorded as snapshots only and never
# allowed to "regress" the OrderState (e.g. a late SUBMITTING push must not
# overwrite a ``filled`` state).
_TERMINAL_OR_FILLED = {"filled", "cancelled", "rejected", "expired", "failed", "reconciled"}

# Broker status text fragments that unambiguously indicate a terminal-negative
# outcome. The default :func:`OrderStateMachine._map_broker_status` ordering
# treats ``filled_qty > 0`` as ``partial_filled`` even when the broker actually
# tells us the order was cancelled with a partial fill in flight (a common
# scenario on Futu when the user/strategy issues cancel_order while a partial
# trade is already in the pipeline). To avoid losing that signal we pre-route
# such pushes to ``cancelled`` / ``rejected`` here.
_CANCEL_HINTS = ("CANCEL", "CXL", "已撤", "撤单", "撤销")
_REJECT_HINTS = ("REJECT", "FAILED", "FAIL", "已拒", "拒单")
_EXPIRE_HINTS = ("EXPIRE", "已失效", "超时")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_broker_status(text: str) -> str | None:
    """Return ``"cancelled"`` / ``"rejected"`` / ``"expired"`` for unambiguous
    terminal-negative pushes; otherwise ``None``.

    The check is intentionally case-insensitive and tolerant of mixed
    Chinese/English broker labels (Futu emits both depending on locale).
    """

    if not text:
        return None
    upper = text.upper()
    for hint in _CANCEL_HINTS:
        if hint in upper or hint in text:
            return "cancelled"
    for hint in _REJECT_HINTS:
        if hint in upper or hint in text:
            return "rejected"
    for hint in _EXPIRE_HINTS:
        if hint in upper or hint in text:
            return "expired"
    return None


def _coerce_status_str(value: Any) -> str:
    """Best-effort string coercion for vn.py ``Status`` enums.

    Status objects have ``.value`` (e.g. ``"已成交"``) and ``.name``
    (``"ALLTRADED"``). We forward both so :meth:`OrderStateMachine._map_broker_status`
    can pattern-match on either Chinese display text or English enum name.
    """

    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    text = getattr(value, "value", None)
    if isinstance(text, str) and text:
        return text
    return str(value)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class OmsEventRecorder:
    """Subscribe to EVENT_ORDER / EVENT_TRADE and persist into OrderStateStore.

    Parameters
    ----------
    store
        The shared :class:`OrderStateStore` (typically an execution-env
        directory such as ``state/runs/dry_run/orders`` or
        ``state/runs/futu_sim/orders``).
    events_log_path
        Optional execution-env scoped events log path (for example
        ``state/runs/dry_run/events.jsonl``). Each recorded transition
        emits a single ``order_status_update`` / ``order_fill`` JSON line.
    machine
        Optional pre-built state machine; defaults to a fresh instance.
    """

    def __init__(
        self,
        store: OrderStateStore,
        *,
        events_log_path: Path | None = None,
        machine: Optional[OrderStateMachine] = None,
    ) -> None:
        self.store = store
        self.events_log_path = Path(events_log_path) if events_log_path else None
        self._machine = machine or OrderStateMachine()
        self._lock = threading.Lock()

        # Dedup buffers (DA).
        self._seen_trade_ids: set[str] = set()
        self._seen_order_pushes: set[tuple[str, str, float]] = set()

        # vt_orderid -> request_id index, rebuilt on attach() from disk.
        self._broker_to_request: dict[str, str] = {}

        # Pending vt_orderids that arrived before their request_id was
        # registered (e.g. EVENT_ORDER fires before strategy returned from
        # ``on_order_submitted``). Stored as the last-seen broker payload
        # so we can replay once the request_id is known.
        self._orphan_orders: dict[str, dict[str, Any]] = {}
        self._orphan_trades: dict[str, list[dict[str, Any]]] = {}

        # Telemetry — useful for tests and diff_dual_run.py.
        self.stats: dict[str, int] = {
            "order_events": 0,
            "trade_events": 0,
            "order_dedup_skips": 0,
            "trade_dedup_skips": 0,
            "orphan_orders": 0,
            "orphan_trades": 0,
            "invalid_transitions": 0,
            "applied_orders": 0,
            "applied_trades": 0,
            "forced_cancelled": 0,
            "forced_rejected": 0,
            "forced_expired": 0,
        }

        self._main_engine = None  # type: ignore[assignment]
        self._event_engine = None  # type: ignore[assignment]
        self._registered = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def attach(self, main_engine: Any) -> None:
        """Subscribe to ``EVENT_ORDER`` / ``EVENT_TRADE`` on the given engine.

        Idempotent: a second call is a no-op (so live_task and
        execution_pipeline can both call attach defensively).
        """
        if self._registered:
            return
        # Local imports to avoid pulling vn.py at module import time.
        from vnpy.trader.event import EVENT_ORDER, EVENT_TRADE

        event_engine = getattr(main_engine, "event_engine", None)
        if event_engine is None:
            raise RuntimeError("main_engine has no event_engine attribute")

        self._main_engine = main_engine
        self._event_engine = event_engine

        # Rebuild vt_orderid -> request_id map from persisted state so we
        # survive process restarts mid-session.
        self._rebuild_index_from_disk()

        event_engine.register(EVENT_ORDER, self._on_order_event)
        event_engine.register(EVENT_TRADE, self._on_trade_event)
        self._registered = True

    def detach(self) -> None:
        """Unregister callbacks. Safe to call multiple times."""
        if not self._registered:
            return
        try:
            from vnpy.trader.event import EVENT_ORDER, EVENT_TRADE

            if self._event_engine is not None:
                self._event_engine.unregister(EVENT_ORDER, self._on_order_event)
                self._event_engine.unregister(EVENT_TRADE, self._on_trade_event)
        except Exception:
            pass
        finally:
            self._registered = False
            self._event_engine = None
            self._main_engine = None

    def register_request(self, vt_orderid: str, request_id: str) -> None:
        """Bind an approved request_id to the vt_orderid returned by send_order.

        Called by ``ExecutionGuardPipeline.on_order_submitted`` immediately
        after the strategy successfully calls ``buy/sell``. If any orphan
        order/trade events arrived earlier under the same vt_orderid, we
        replay them now.
        """
        if not vt_orderid or not request_id:
            return
        with self._lock:
            self._broker_to_request[vt_orderid] = request_id
            orphan_order = self._orphan_orders.pop(vt_orderid, None)
            orphan_trades = self._orphan_trades.pop(vt_orderid, [])

        if orphan_order is not None:
            self._apply_order_payload(vt_orderid, request_id, orphan_order)
        for payload in orphan_trades:
            self._apply_trade_payload(vt_orderid, request_id, payload)

    # ------------------------------------------------------------------
    # Internal — vn.py event handlers
    # ------------------------------------------------------------------
    def _on_order_event(self, event: Any) -> None:
        order = getattr(event, "data", None) or event
        try:
            payload = self._extract_order_payload(order)
        except Exception:
            self.stats["invalid_transitions"] += 1
            return

        vt_orderid = payload["vt_orderid"]
        if not vt_orderid:
            return

        # Dedup: identical (vt_orderid, status, traded) seen before.
        dedup_key = (vt_orderid, payload["status_text"], float(payload["traded"]))
        with self._lock:
            if dedup_key in self._seen_order_pushes:
                self.stats["order_dedup_skips"] += 1
                return
            self._seen_order_pushes.add(dedup_key)

        self.stats["order_events"] += 1

        request_id = self._broker_to_request.get(vt_orderid)
        if request_id is None:
            with self._lock:
                self._orphan_orders[vt_orderid] = payload
                self.stats["orphan_orders"] += 1
            self._append_event(
                {
                    "ts": _utc_iso(),
                    "event": "order_status_orphan",
                    "vt_orderid": vt_orderid,
                    "status": payload["status_text"],
                }
            )
            return
        self._apply_order_payload(vt_orderid, request_id, payload)

    def _on_trade_event(self, event: Any) -> None:
        trade = getattr(event, "data", None) or event
        try:
            payload = self._extract_trade_payload(trade)
        except Exception:
            self.stats["invalid_transitions"] += 1
            return

        vt_tradeid = payload["vt_tradeid"]
        vt_orderid = payload["vt_orderid"]
        if not vt_tradeid or not vt_orderid:
            return

        with self._lock:
            if vt_tradeid in self._seen_trade_ids:
                self.stats["trade_dedup_skips"] += 1
                return
            self._seen_trade_ids.add(vt_tradeid)

        self.stats["trade_events"] += 1

        request_id = self._broker_to_request.get(vt_orderid)
        if request_id is None:
            with self._lock:
                self._orphan_trades.setdefault(vt_orderid, []).append(payload)
                self.stats["orphan_trades"] += 1
            self._append_event(
                {
                    "ts": _utc_iso(),
                    "event": "order_fill_orphan",
                    "vt_orderid": vt_orderid,
                    "vt_tradeid": vt_tradeid,
                    "volume": payload["volume"],
                }
            )
            return
        self._apply_trade_payload(vt_orderid, request_id, payload)

    # ------------------------------------------------------------------
    # Internal — payload extraction
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_order_payload(order: Any) -> dict[str, Any]:
        return {
            "vt_orderid": str(getattr(order, "vt_orderid", "") or ""),
            "orderid": str(getattr(order, "orderid", "") or ""),
            "status": getattr(order, "status", None),
            "status_text": _coerce_status_str(getattr(order, "status", None)),
            "volume": float(getattr(order, "volume", 0) or 0),
            "traded": float(getattr(order, "traded", 0) or 0),
            "price": float(getattr(order, "price", 0) or 0) or None,
            "datetime": getattr(order, "datetime", None),
        }

    @staticmethod
    def _extract_trade_payload(trade: Any) -> dict[str, Any]:
        return {
            "vt_orderid": str(getattr(trade, "vt_orderid", "") or ""),
            "vt_tradeid": str(getattr(trade, "vt_tradeid", "") or ""),
            "tradeid": str(getattr(trade, "tradeid", "") or ""),
            "volume": float(getattr(trade, "volume", 0) or 0),
            "price": float(getattr(trade, "price", 0) or 0) or None,
            "datetime": getattr(trade, "datetime", None),
        }

    # ------------------------------------------------------------------
    # Internal — apply to OrderStateStore
    # ------------------------------------------------------------------
    def _apply_order_payload(
        self, vt_orderid: str, request_id: str, payload: dict[str, Any]
    ) -> None:
        from dataclasses import replace as _replace

        with self._lock:
            state = self.store.load(request_id)
            if state is None:
                # Approved state was never persisted (very rare — would
                # mean execution_pipeline crashed mid-approve). Bail out
                # rather than fabricating a state out of thin air.
                self.stats["invalid_transitions"] += 1
                return

            # Detect terminal-negative pushes BEFORE the bridge below: a
            # rejection right after ``approved`` must NOT first walk
            # through ``submitted`` (the broker is telling us the order
            # was never accepted in the first place).
            terminal_neg = _classify_broker_status(payload["status_text"])

            # Bridge approved -> submitted so subsequent broker statuses
            # have a legal predecessor (the pre-trade pipeline stops at
            # ``approved``; the state machine forbids approved -> filled,
            # approved -> cancelled and approved -> rejected directly).
            # Always stamp broker_order_id when we first see it.
            if state.status == "approved":
                try:
                    state = self._machine.transition(
                        state, "submitted", note=f"oms_recorder:bridge:{vt_orderid}"
                    )
                    state = _replace(state, broker_order_id=vt_orderid)
                    self.store.save(state)
                except InvalidOrderTransition:
                    pass

            # Late pushes for already-terminal orders are recorded as a
            # snapshot only; never overwrite ``filled``/``cancelled``.
            if state.status in _TERMINAL_OR_FILLED and not _is_progressing_status(
                state.status, payload["status_text"], int(payload["traded"]), int(state.qty)
            ):
                self._record_snapshot(state, "order_status_late_push", payload)
                return

            # Pre-route terminal-negative pushes that the default mapper
            # would otherwise classify as ``partial_filled`` (because
            # filled_qty > 0). The state machine allows submitted /
            # partial_filled / cancel_requested -> cancelled, so the
            # forced transition is legal once we are past ``approved``.
            forced_status: str | None = None
            if terminal_neg in {"cancelled", "rejected", "expired"}:
                # filled_qty must NEVER regress: keep whichever is higher
                # between the broker's reported traded and our persisted
                # state.filled_qty (idempotent re-delivery protection).
                effective_filled = max(int(state.filled_qty), int(payload["traded"]))
                forced_status = terminal_neg
            else:
                effective_filled = int(payload["traded"])

            try:
                if forced_status is not None:
                    next_state = self._machine.transition(
                        state,
                        forced_status,  # type: ignore[arg-type]
                        note=f"oms_recorder:forced:{payload['status_text']}",
                        snapshot={
                            "source": "EVENT_ORDER",
                            "ts": _utc_iso(),
                            "vt_orderid": vt_orderid,
                            "status": payload["status_text"],
                            "traded": payload["traded"],
                            "volume": payload["volume"],
                            "forced": forced_status,
                        },
                    )
                    next_state = _replace(
                        next_state,
                        broker_order_id=vt_orderid,
                        filled_qty=max(next_state.filled_qty, effective_filled),
                        avg_fill_price=(
                            payload.get("price")
                            if payload.get("price") is not None
                            else next_state.avg_fill_price
                        ),
                    )
                    self.stats[f"forced_{forced_status}"] = (
                        self.stats.get(f"forced_{forced_status}", 0) + 1
                    )
                else:
                    next_state = self._machine.apply_broker_order(
                        state,
                        broker_order_id=vt_orderid,
                        broker_status=payload["status_text"],
                        filled_qty=effective_filled,
                        avg_fill_price=payload.get("price"),
                        snapshot={
                            "source": "EVENT_ORDER",
                            "ts": _utc_iso(),
                            "vt_orderid": vt_orderid,
                            "status": payload["status_text"],
                            "traded": payload["traded"],
                            "volume": payload["volume"],
                        },
                    )
            except InvalidOrderTransition:
                # Out-of-order push: log as snapshot but don't mutate status.
                self.stats["invalid_transitions"] += 1
                self._record_snapshot(state, "order_status_invalid_transition", payload)
                return

            self.store.save(next_state)
            self.stats["applied_orders"] += 1

        self._append_event(
            {
                "ts": _utc_iso(),
                "event": "order_status_update",
                "request_id": request_id,
                "vt_orderid": vt_orderid,
                "status": payload["status_text"],
                "resolved_status": next_state.status,
                "filled_qty": int(next_state.filled_qty),
                "qty": int(payload["volume"]),
            }
        )

    def _apply_trade_payload(
        self, vt_orderid: str, request_id: str, payload: dict[str, Any]
    ) -> None:
        with self._lock:
            state = self.store.load(request_id)
            if state is None:
                self.stats["invalid_transitions"] += 1
                return

            # Bridge approved -> submitted before applying any fill so
            # the state machine can reach partial_filled / filled. Stamp
            # broker_order_id so a later restart can rebuild the index.
            if state.status == "approved":
                try:
                    state = self._machine.transition(
                        state, "submitted", note=f"oms_recorder:bridge_trade:{vt_orderid}"
                    )
                    from dataclasses import replace as _replace

                    state = _replace(state, broker_order_id=vt_orderid)
                    self.store.save(state)
                except InvalidOrderTransition:
                    pass

            new_filled = int(state.filled_qty) + int(payload["volume"])
            new_filled = min(new_filled, int(state.qty)) if state.qty > 0 else new_filled

            # Weighted average fill price (only update when we have a price).
            new_avg = state.avg_fill_price
            trade_price = payload.get("price")
            if trade_price is not None:
                if state.avg_fill_price is None or state.filled_qty <= 0:
                    new_avg = float(trade_price)
                else:
                    prev_total = float(state.avg_fill_price) * float(state.filled_qty)
                    cur_total = float(trade_price) * float(payload["volume"])
                    new_avg = (prev_total + cur_total) / max(new_filled, 1)

            try:
                next_state = self._machine.apply_broker_order(
                    state,
                    broker_order_id=vt_orderid,
                    broker_status="ALLTRADED" if new_filled >= int(state.qty) > 0 else "PARTTRADED",
                    filled_qty=new_filled,
                    avg_fill_price=new_avg,
                    snapshot={
                        "source": "EVENT_TRADE",
                        "ts": _utc_iso(),
                        "vt_tradeid": payload["vt_tradeid"],
                        "volume": payload["volume"],
                        "price": payload.get("price"),
                    },
                )
            except InvalidOrderTransition:
                self.stats["invalid_transitions"] += 1
                self._record_snapshot(state, "order_fill_invalid_transition", payload)
                return

            self.store.save(next_state)
            self.stats["applied_trades"] += 1

        self._append_event(
            {
                "ts": _utc_iso(),
                "event": "order_fill",
                "request_id": request_id,
                "vt_orderid": vt_orderid,
                "vt_tradeid": payload["vt_tradeid"],
                "filled_qty": int(new_filled),
                "trade_volume": int(payload["volume"]),
                "trade_price": payload.get("price"),
            }
        )

    # ------------------------------------------------------------------
    # Internal — utilities
    # ------------------------------------------------------------------
    def _rebuild_index_from_disk(self) -> None:
        """Re-load vt_orderid -> request_id binding from persisted state."""
        try:
            states = self.store.list()
        except Exception:
            states = []
        with self._lock:
            self._broker_to_request.clear()
            for state in states:
                if state.broker_order_id and state.request_id:
                    self._broker_to_request[state.broker_order_id] = state.request_id

    def _record_snapshot(
        self, state: OrderState, label: str, payload: dict[str, Any]
    ) -> None:
        snapshots = dict(state.snapshots)
        snapshots.setdefault("late_events", []).append(
            {
                "ts": _utc_iso(),
                "label": label,
                **{k: v for k, v in payload.items() if k != "datetime"},
            }
        )
        from dataclasses import replace as _replace

        self.store.save(_replace(state, snapshots=snapshots))

    def _append_event(self, payload: dict[str, Any]) -> None:
        if self.events_log_path is None:
            return
        try:
            self.events_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_log_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass


def _is_progressing_status(
    current: str, incoming_text: str, incoming_filled: int, qty: int
) -> bool:
    """Return True iff the incoming push represents forward progress.

    Used to decide whether a "late" push for an already-terminal order
    should be ignored (False) or applied (True). Currently only allows
    reconciled progression — every other late push is recorded as a
    snapshot.
    """

    text = incoming_text.upper()
    if current == "filled" and qty > 0 and incoming_filled >= qty:
        return False
    if current in {"cancelled", "rejected", "expired", "failed"}:
        return False
    if current == "reconciled":
        return False
    # Defensive: only accept the push if it would be a legal forward edge.
    from .state_machine import ALLOWED_TRANSITIONS as _AT

    legal = _AT.get(current, set())
    return bool(legal)


__all__ = ["OmsEventRecorder"]
