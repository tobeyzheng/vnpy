"""Futu OpenD implementation of the LiveBroker protocol.

The implementation deliberately defers ``import futu`` to method call time so
the rest of phase2 (and the entire test suite) can import this module on
machines without ``futu`` installed. ``InMemoryBroker`` in
``phase2/live/tests/test_broker.py`` exercises the same protocol without ever
touching OpenD.

REAL-vs-SIM differences are concentrated in two places:

1. ``connect()`` records ``trd_env`` once at construction (``TrdEnv.SIMULATE``
   for futu_sim, ``TrdEnv.REAL`` for futu_real).
2. ``unlock_trade()`` is a *no-op* in SIM (returns True without contacting
   OpenD); in REAL it calls ``ctx.unlock_trade(password=...)`` and refuses to
   place orders until it succeeds.

Reconnection: ``place_order`` raises ``BrokerNotReady`` if the underlying
context is not connected; the runner catches it, records a
``connection_lost`` event, and triggers exponential-backoff reconnect (up to
5 attempts) before retrying.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from phase2.live.broker import (
    BrokerAccount,
    BrokerOrderAck,
    BrokerOrderUpdate,
    BrokerPosition,
    OrderHandlerCallback,
)
from phase2.live.order_state import OrderIntent

logger = logging.getLogger(__name__)

__all__ = ["FutuBrokerConfig", "FutuBroker", "BrokerNotReady"]


class BrokerNotReady(RuntimeError):
    """Raised when an op is attempted before connect()/unlock_trade() succeeded."""


@dataclass
class FutuBrokerConfig:
    host: str
    port: int
    market: str = "US"  # phase2 only supports US in this milestone
    execution_env: str = "futu_sim"  # "futu_sim" | "futu_real"
    trd_password: str | None = None
    acc_id: int = 0
    acc_index: int = 0
    security_firm: str = "FUTUSECURITIES"
    max_reconnect_attempts: int = 5
    reconnect_base_delay: float = 1.0  # seconds


# ---------------------------------------------------------------------------
# Helpers — small adapters over futu enums kept inside the class so importing
# this module never imports futu.
# ---------------------------------------------------------------------------

def _import_futu() -> Any:
    import futu as ft  # local import; heavy

    return ft


class FutuBroker:
    """Concrete LiveBroker backed by Futu OpenD.

    ``execution_env`` must be one of ``futu_sim`` or ``futu_real``. dry_run
    callers should not instantiate this class — they should use
    ``InMemoryBroker`` instead (the runner picks based on execution_env).
    """

    def __init__(self, config: FutuBrokerConfig) -> None:
        if config.execution_env not in {"futu_sim", "futu_real"}:
            raise ValueError(
                f"FutuBroker only supports futu_sim/futu_real, "
                f"got execution_env={config.execution_env!r}"
            )
        if config.execution_env == "futu_real" and not config.trd_password:
            raise ValueError("futu_real broker requires a non-empty trd_password")
        self.config = config
        self.execution_env = config.execution_env
        self._trd_ctx: Any | None = None
        self._quote_ctx: Any | None = None
        self._unlocked: bool = config.execution_env == "futu_sim"
        self._handler_cb: OrderHandlerCallback | None = None
        self._handler_installed: bool = False

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------
    def connect(self) -> None:
        ft = _import_futu()
        market_map = {"US": ft.TrdMarket.US, "HK": ft.TrdMarket.HK}
        market = market_map.get(self.config.market.upper())
        if market is None:
            raise ValueError(
                f"unsupported market for FutuBroker: {self.config.market!r}"
            )
        firm_map = {
            "FUTUSECURITIES": getattr(ft.SecurityFirm, "FUTUSECURITIES", None),
            "FUTUINC": getattr(ft.SecurityFirm, "FUTUINC", None),
        }
        firm = firm_map.get(self.config.security_firm.upper())
        kwargs: dict[str, Any] = {
            "filter_trdmarket": market,
            "host": self.config.host,
            "port": int(self.config.port),
        }
        if firm is not None:
            kwargs["security_firm"] = firm
        last_err: Exception | None = None
        for attempt in range(1, self.config.max_reconnect_attempts + 1):
            try:
                self._trd_ctx = ft.OpenSecTradeContext(**kwargs)
                self._quote_ctx = ft.OpenQuoteContext(
                    host=self.config.host, port=int(self.config.port)
                )
                logger.info(
                    "futu broker connected (env=%s, host=%s, port=%s, attempt=%d)",
                    self.execution_env,
                    self.config.host,
                    self.config.port,
                    attempt,
                )
                return
            except Exception as exc:  # pragma: no cover - real network only
                last_err = exc
                delay = self.config.reconnect_base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "futu broker connect attempt %d/%d failed: %s; sleeping %.1fs",
                    attempt,
                    self.config.max_reconnect_attempts,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise BrokerNotReady(
            f"futu broker failed to connect after "
            f"{self.config.max_reconnect_attempts} attempts: {last_err!r}"
        )

    def disconnect(self) -> None:
        for ctx_attr in ("_trd_ctx", "_quote_ctx"):
            ctx = getattr(self, ctx_attr, None)
            if ctx is not None:
                try:
                    ctx.close()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("error closing %s: %s", ctx_attr, exc)
                setattr(self, ctx_attr, None)
        self._unlocked = self.execution_env == "futu_sim"

    # ------------------------------------------------------------------
    # auth
    # ------------------------------------------------------------------
    def unlock_trade(self) -> bool:
        if self.execution_env == "futu_sim":
            self._unlocked = True
            return True
        # REAL: must call OpenD with the password.
        ft = _import_futu()
        self._require_trd_ctx()
        ret, data = self._trd_ctx.unlock_trade(password=self.config.trd_password)
        ok = ret == ft.RET_OK
        if not ok:
            logger.error("unlock_trade failed: %r", data)
        self._unlocked = ok
        return ok

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def query_account(self) -> BrokerAccount:
        ft = _import_futu()
        self._require_trd_ctx()
        ret, data = self._trd_ctx.accinfo_query(
            trd_env=self._trd_env_value(),
            acc_id=self.config.acc_id,
            acc_index=self.config.acc_index,
            currency=getattr(ft.Currency, "USD", 0),
        )
        if ret != ft.RET_OK:
            raise BrokerNotReady(f"accinfo_query failed: {data!r}")
        # data is a pandas-like DataFrame; first row holds the account.
        row = data.iloc[0].to_dict() if hasattr(data, "iloc") else data[0]
        return BrokerAccount(
            cash=float(row.get("cash", 0.0)),
            market_value=float(row.get("market_val", 0.0)),
            total_assets=float(row.get("total_assets", 0.0)),
            currency=str(row.get("currency", "USD")),
            raw=dict(row),
        )

    def query_positions(self) -> list[BrokerPosition]:
        ft = _import_futu()
        self._require_trd_ctx()
        ret, data = self._trd_ctx.position_list_query(
            trd_env=self._trd_env_value(),
            acc_id=self.config.acc_id,
            acc_index=self.config.acc_index,
        )
        if ret != ft.RET_OK:
            raise BrokerNotReady(f"position_list_query failed: {data!r}")
        rows: list[dict[str, Any]]
        if hasattr(data, "to_dict"):
            rows = data.to_dict("records")
        else:
            rows = list(data)
        out: list[BrokerPosition] = []
        for row in rows:
            qty = int(float(row.get("qty", 0)))
            if qty == 0:
                continue
            out.append(
                BrokerPosition(
                    symbol=str(row.get("code", "")),
                    qty=qty,
                    avg_cost=float(row.get("cost_price", 0.0)),
                    market_value=float(row.get("market_val", 0.0)),
                    currency=str(row.get("currency", "USD")),
                    raw=dict(row),
                )
            )
        return out

    # ------------------------------------------------------------------
    # ordering
    # ------------------------------------------------------------------
    def place_order(self, intent: OrderIntent) -> BrokerOrderAck:
        ft = _import_futu()
        self._require_trd_ctx()
        if self.execution_env == "futu_real" and not self._unlocked:
            raise BrokerNotReady(
                "futu_real broker requires unlock_trade() before place_order()"
            )
        side = ft.TrdSide.BUY if intent.side == "BUY" else ft.TrdSide.SELL
        order_type = ft.OrderType.NORMAL  # phase2 daily uses LIMIT (NORMAL in futu)
        price = float(intent.price) if intent.price is not None else 0.0
        ret, data = self._trd_ctx.place_order(
            price=price,
            qty=int(intent.qty),
            code=intent.symbol,
            trd_side=side,
            order_type=order_type,
            trd_env=self._trd_env_value(),
            acc_id=self.config.acc_id,
            acc_index=self.config.acc_index,
            remark=intent.request_id[:32],  # futu remark length cap
        )
        if ret != ft.RET_OK:
            return BrokerOrderAck(
                request_id=intent.request_id,
                broker_order_id="",
                accepted_qty=0,
                status="rejected",
                message=str(data),
            )
        row = data.iloc[0].to_dict() if hasattr(data, "iloc") else data[0]
        return BrokerOrderAck(
            request_id=intent.request_id,
            broker_order_id=str(row.get("order_id", "")),
            accepted_qty=int(float(row.get("qty", intent.qty))),
            status="submitted",
            message=str(row.get("order_status", "")),
            raw=dict(row),
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        ft = _import_futu()
        self._require_trd_ctx()
        if self.execution_env == "futu_real" and not self._unlocked:
            raise BrokerNotReady(
                "futu_real broker requires unlock_trade() before cancel_order()"
            )
        ret, data = self._trd_ctx.modify_order(
            modify_order_op=ft.ModifyOrderOp.CANCEL,
            order_id=broker_order_id,
            qty=0,
            price=0,
            trd_env=self._trd_env_value(),
            acc_id=self.config.acc_id,
            acc_index=self.config.acc_index,
        )
        if ret != ft.RET_OK:
            logger.warning("cancel_order failed for %s: %r", broker_order_id, data)
            return False
        return True

    # ------------------------------------------------------------------
    # quote subscription / order callback
    # ------------------------------------------------------------------
    def subscribe_quote(self, symbols: Iterable[str]) -> None:
        ft = _import_futu()
        if self._quote_ctx is None:
            raise BrokerNotReady("connect() must be called before subscribe_quote()")
        codes = list(symbols)
        if not codes:
            return
        ret, data = self._quote_ctx.subscribe(codes, [ft.SubType.K_DAY])
        if ret != ft.RET_OK:
            raise BrokerNotReady(f"subscribe_quote failed: {data!r}")

    def register_order_handler(self, callback: OrderHandlerCallback) -> None:
        self._handler_cb = callback
        if self._trd_ctx is None or self._handler_installed:
            return
        self._install_handler()

    def _install_handler(self) -> None:
        ft = _import_futu()
        broker_self = self

        class _Handler(ft.TradeOrderHandlerBase):  # type: ignore[misc]
            def on_recv_rsp(self, rsp_pb):  # noqa: D401 - futu callback
                ret, content = super().on_recv_rsp(rsp_pb)
                cb = broker_self._handler_cb
                if cb is None or ret != ft.RET_OK:
                    return ret, content
                rows = (
                    content.to_dict("records") if hasattr(content, "to_dict") else [content]
                )
                for row in rows:
                    cb(_row_to_update(row))
                return ret, content

        self._trd_ctx.set_handler(_Handler())
        self._handler_installed = True

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------
    def _trd_env_value(self) -> Any:
        ft = _import_futu()
        return ft.TrdEnv.REAL if self.execution_env == "futu_real" else ft.TrdEnv.SIMULATE

    def _require_trd_ctx(self) -> None:
        if self._trd_ctx is None:
            raise BrokerNotReady("FutuBroker.connect() must succeed first")


# ---------------------------------------------------------------------------
# Update row → BrokerOrderUpdate (kept module-level so unit tests can inject
# row dicts without futu import).
# ---------------------------------------------------------------------------

_FUTU_STATUS_MAP: dict[str, str] = {
    "SUBMITTING": "submitting",
    "SUBMITTED": "submitted",
    "FILLED_PART": "partial_filled",
    "FILLED_ALL": "filled",
    "CANCELLED_PART": "partial_filled",
    "CANCELLED_ALL": "cancelled",
    "FAILED": "failed",
    "DISABLED": "rejected",
    "DELETED": "cancelled",
    "WAITING_SUBMIT": "submitting",
}


def _row_to_update(row: dict[str, Any]) -> BrokerOrderUpdate:
    raw_status = str(row.get("order_status", "") or "").upper()
    return BrokerOrderUpdate(
        broker_order_id=str(row.get("order_id", "")),
        request_id=str(row.get("remark", "")) or None,
        symbol=str(row.get("code", "")),
        status=_FUTU_STATUS_MAP.get(raw_status, "submitting"),
        filled_qty=int(float(row.get("dealt_qty", 0))),
        avg_fill_price=float(row.get("dealt_avg_price", 0.0)),
        raw=dict(row),
    )
