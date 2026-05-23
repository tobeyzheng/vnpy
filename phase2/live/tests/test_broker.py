"""Unit tests for phase2.live.broker / futu_broker.

Strategy: we never import the real ``futu`` package. Instead we build a tiny
``FakeFutu`` namespace, register it as ``sys.modules['futu']``, and let
``FutuBroker._import_futu`` pick it up. This both keeps the test fast and
proves the broker code path doesn't accidentally rely on undocumented futu
internals.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from phase2.live.broker import (
    BrokerAccount,
    BrokerOrderAck,
    BrokerOrderUpdate,
    BrokerPosition,
    LiveBroker,
)
from phase2.live.order_state import make_intent


# ---------------------------------------------------------------------------
# FakeFutu module
# ---------------------------------------------------------------------------

RET_OK = 0
RET_ERROR = -1


class _Enum:
    def __init__(self, name): self.name = name
    def __repr__(self): return f"<Enum {self.name}>"


class _TrdMarket: US = _Enum("US"); HK = _Enum("HK"); NONE = _Enum("NONE")
class _TrdEnv: SIMULATE = _Enum("SIMULATE"); REAL = _Enum("REAL")
class _TrdSide: BUY = _Enum("BUY"); SELL = _Enum("SELL")
class _OrderType: NORMAL = _Enum("NORMAL")
class _ModifyOrderOp: CANCEL = _Enum("CANCEL")
class _SubType: K_DAY = _Enum("K_DAY")
class _SecurityFirm: FUTUSECURITIES = _Enum("FUTUSECURITIES"); FUTUINC = _Enum("FUTUINC")
class _Currency: USD = _Enum("USD"); HKD = _Enum("HKD")


class _DataFrame:
    """Minimal DataFrame stand-in supporting .iloc[0].to_dict and to_dict('records')."""

    def __init__(self, rows):
        self._rows = list(rows)

    @property
    def iloc(self):
        return _Iloc(self._rows)

    def to_dict(self, orient: str = "records"):
        return [dict(r) for r in self._rows]


class _Iloc:
    def __init__(self, rows): self._rows = rows
    def __getitem__(self, idx):
        @dataclass
        class _Row:
            _data: dict
            def to_dict(self):  # noqa: D401
                return dict(self._data)
        return _Row(self._rows[idx])


class _TradeOrderHandlerBase:
    """Stand-in for futu.TradeOrderHandlerBase."""

    def on_recv_rsp(self, rsp_pb):  # pragma: no cover - inherited
        return RET_OK, rsp_pb


class _FakeTrdCtx:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.unlock_calls: list[dict] = []
        self.place_calls: list[dict] = []
        self.cancel_calls: list[dict] = []
        self.handler = None
        self.closed = False
        self.unlock_ok = True

    def unlock_trade(self, password=None, password_md5=None, is_unlock=True):
        self.unlock_calls.append(
            {"password": password, "is_unlock": is_unlock}
        )
        return (RET_OK if self.unlock_ok else RET_ERROR), "ok" if self.unlock_ok else "bad"

    def accinfo_query(self, **kw):
        return RET_OK, _DataFrame([
            {"cash": 100000.0, "market_val": 50000.0, "total_assets": 150000.0, "currency": "USD"}
        ])

    def position_list_query(self, **kw):
        return RET_OK, _DataFrame([
            {"code": "US.AAPL", "qty": 10, "cost_price": 100.0, "market_val": 1500.0, "currency": "USD"},
            {"code": "US.NVDA", "qty": 0, "cost_price": 0.0, "market_val": 0.0, "currency": "USD"},
        ])

    def place_order(self, **kw):
        self.place_calls.append(kw)
        return RET_OK, _DataFrame([{
            "order_id": f"BRK-{len(self.place_calls)}",
            "qty": kw["qty"],
            "order_status": "SUBMITTED",
            "code": kw["code"],
        }])

    def modify_order(self, **kw):
        self.cancel_calls.append(kw)
        return RET_OK, _DataFrame([{"order_id": kw["order_id"], "order_status": "CANCELLED_ALL"}])

    def set_handler(self, handler):
        self.handler = handler

    def close(self):
        self.closed = True


class _FakeQuoteCtx:
    def __init__(self, host=None, port=None):
        self.host = host
        self.port = port
        self.subscribed: list[tuple] = []
        self.closed = False

    def subscribe(self, codes, types):
        self.subscribed.append((tuple(codes), tuple(types)))
        return RET_OK, "ok"

    def close(self):
        self.closed = True


def _install_fake_futu(monkeypatch, *, last_trd_ctx_holder: list | None = None):
    fake = types.ModuleType("futu")
    fake.RET_OK = RET_OK
    fake.RET_ERROR = RET_ERROR
    fake.TrdMarket = _TrdMarket
    fake.TrdEnv = _TrdEnv
    fake.TrdSide = _TrdSide
    fake.OrderType = _OrderType
    fake.ModifyOrderOp = _ModifyOrderOp
    fake.SubType = _SubType
    fake.SecurityFirm = _SecurityFirm
    fake.Currency = _Currency
    fake.TradeOrderHandlerBase = _TradeOrderHandlerBase

    def _open_trd(**kwargs):
        ctx = _FakeTrdCtx(**kwargs)
        if last_trd_ctx_holder is not None:
            last_trd_ctx_holder.append(ctx)
        return ctx

    fake.OpenSecTradeContext = _open_trd
    fake.OpenQuoteContext = _FakeQuoteCtx
    monkeypatch.setitem(sys.modules, "futu", fake)
    return fake


# ---------------------------------------------------------------------------
# Protocol shape
# ---------------------------------------------------------------------------

def test_live_broker_is_protocol():
    # Any object with the 9 attributes should runtime_checkable as LiveBroker.
    class Mock:
        execution_env = "dry_run"
        def connect(self): pass
        def disconnect(self): pass
        def unlock_trade(self): return True
        def query_account(self): return BrokerAccount(0, 0, 0)
        def query_positions(self): return []
        def place_order(self, intent): return BrokerOrderAck(intent.request_id, "x", 0, "submitted")
        def cancel_order(self, broker_order_id): return True
        def subscribe_quote(self, symbols): pass
        def register_order_handler(self, cb): pass

    assert isinstance(Mock(), LiveBroker)


# ---------------------------------------------------------------------------
# FutuBroker SIM path
# ---------------------------------------------------------------------------

class TestFutuBrokerSim:
    def _broker(self, monkeypatch):
        from phase2.live.futu_broker import FutuBroker, FutuBrokerConfig
        holder: list = []
        _install_fake_futu(monkeypatch, last_trd_ctx_holder=holder)
        b = FutuBroker(FutuBrokerConfig(
            host="127.0.0.1", port=11111, market="US", execution_env="futu_sim",
        ))
        b.connect()
        return b, holder[-1]

    def test_sim_unlock_no_op_does_not_call_futu(self, monkeypatch):
        b, ctx = self._broker(monkeypatch)
        assert b.unlock_trade() is True
        assert ctx.unlock_calls == []  # MUST NOT contact OpenD in SIM

    def test_sim_place_order_uses_simulate(self, monkeypatch):
        b, ctx = self._broker(monkeypatch)
        intent = make_intent(
            strategy_id="phase2", symbol="US.AAPL", market="US", side="BUY",
            qty=10, price=100.0, rebalance_date="2026-05-20",
            execution_env="futu_sim",
        )
        ack = b.place_order(intent)
        assert ack.status == "submitted"
        assert ack.broker_order_id == "BRK-1"
        assert ctx.place_calls[0]["trd_env"] is _TrdEnv.SIMULATE
        assert ctx.place_calls[0]["code"] == "US.AAPL"
        assert ctx.place_calls[0]["qty"] == 10

    def test_sim_query_account_and_positions(self, monkeypatch):
        b, _ = self._broker(monkeypatch)
        acc = b.query_account()
        assert isinstance(acc, BrokerAccount)
        assert acc.cash == 100000.0
        positions = b.query_positions()
        # zero-qty positions filtered out
        assert len(positions) == 1
        assert positions[0].symbol == "US.AAPL"
        assert positions[0].qty == 10

    def test_sim_cancel_order(self, monkeypatch):
        b, ctx = self._broker(monkeypatch)
        ok = b.cancel_order("BRK-1")
        assert ok is True
        assert ctx.cancel_calls[0]["order_id"] == "BRK-1"
        assert ctx.cancel_calls[0]["modify_order_op"] is _ModifyOrderOp.CANCEL
        assert ctx.cancel_calls[0]["trd_env"] is _TrdEnv.SIMULATE

    def test_subscribe_quote(self, monkeypatch):
        b, _ = self._broker(monkeypatch)
        b.subscribe_quote(["US.AAPL", "US.NVDA"])
        assert b._quote_ctx.subscribed == [
            (("US.AAPL", "US.NVDA"), (_SubType.K_DAY,))
        ]

    def test_register_order_handler_routes_updates(self, monkeypatch):
        b, ctx = self._broker(monkeypatch)
        received: list[BrokerOrderUpdate] = []
        b.register_order_handler(received.append)
        # Simulate a futu callback delivering an order fill.
        # The handler set on ctx wraps base.on_recv_rsp; we feed it a DataFrame.
        rsp = _DataFrame([{
            "order_id": "BRK-1", "remark": "p2live-abc", "code": "US.AAPL",
            "order_status": "FILLED_ALL", "dealt_qty": 10, "dealt_avg_price": 99.5,
        }])
        ctx.handler.on_recv_rsp(rsp)
        assert len(received) == 1
        upd = received[0]
        assert upd.broker_order_id == "BRK-1"
        assert upd.status == "filled"
        assert upd.filled_qty == 10

    def test_disconnect_closes_contexts(self, monkeypatch):
        b, ctx = self._broker(monkeypatch)
        qctx = b._quote_ctx
        b.disconnect()
        assert ctx.closed is True
        assert qctx.closed is True
        assert b._trd_ctx is None and b._quote_ctx is None


# ---------------------------------------------------------------------------
# FutuBroker REAL path
# ---------------------------------------------------------------------------

class TestFutuBrokerReal:
    def _broker(self, monkeypatch, *, password="real-pwd", unlock_ok=True):
        from phase2.live.futu_broker import FutuBroker, FutuBrokerConfig
        holder: list = []
        _install_fake_futu(monkeypatch, last_trd_ctx_holder=holder)
        b = FutuBroker(FutuBrokerConfig(
            host="127.0.0.1", port=11111, market="US",
            execution_env="futu_real", trd_password=password,
        ))
        b.connect()
        ctx = holder[-1]
        ctx.unlock_ok = unlock_ok
        return b, ctx

    def test_real_requires_password(self, monkeypatch):
        from phase2.live.futu_broker import FutuBroker, FutuBrokerConfig
        _install_fake_futu(monkeypatch)
        with pytest.raises(ValueError):
            FutuBroker(FutuBrokerConfig(
                host="127.0.0.1", port=11111, market="US",
                execution_env="futu_real", trd_password="",
            ))

    def test_real_place_order_blocked_until_unlock(self, monkeypatch):
        from phase2.live.futu_broker import BrokerNotReady
        b, _ctx = self._broker(monkeypatch)
        intent = make_intent(
            strategy_id="phase2", symbol="US.AAPL", market="US", side="BUY",
            qty=10, price=100.0, rebalance_date="2026-05-20",
            execution_env="futu_real",
        )
        with pytest.raises(BrokerNotReady):
            b.place_order(intent)

    def test_real_unlock_then_place_uses_real(self, monkeypatch):
        b, ctx = self._broker(monkeypatch, password="real-pwd")
        assert b.unlock_trade() is True
        assert ctx.unlock_calls[0]["password"] == "real-pwd"
        intent = make_intent(
            strategy_id="phase2", symbol="US.AAPL", market="US", side="SELL",
            qty=5, price=100.0, rebalance_date="2026-05-20",
            execution_env="futu_real",
        )
        ack = b.place_order(intent)
        assert ack.status == "submitted"
        assert ctx.place_calls[0]["trd_env"] is _TrdEnv.REAL
        assert ctx.place_calls[0]["trd_side"] is _TrdSide.SELL

    def test_real_unlock_failure_blocks_orders(self, monkeypatch):
        from phase2.live.futu_broker import BrokerNotReady
        b, _ = self._broker(monkeypatch, unlock_ok=False)
        assert b.unlock_trade() is False
        intent = make_intent(
            strategy_id="phase2", symbol="US.AAPL", market="US", side="BUY",
            qty=10, price=100.0, rebalance_date="2026-05-20",
            execution_env="futu_real",
        )
        with pytest.raises(BrokerNotReady):
            b.place_order(intent)

    def test_real_cancel_blocked_until_unlock(self, monkeypatch):
        from phase2.live.futu_broker import BrokerNotReady
        b, _ = self._broker(monkeypatch)
        with pytest.raises(BrokerNotReady):
            b.cancel_order("BRK-1")
        assert b.unlock_trade() is True
        assert b.cancel_order("BRK-1") is True


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def test_invalid_execution_env_rejected(monkeypatch):
    from phase2.live.futu_broker import FutuBroker, FutuBrokerConfig
    _install_fake_futu(monkeypatch)
    with pytest.raises(ValueError):
        FutuBroker(FutuBrokerConfig(
            host="127.0.0.1", port=11111, market="US",
            execution_env="dry_run",
        ))


def test_place_order_remark_truncated(monkeypatch):
    from phase2.live.futu_broker import FutuBroker, FutuBrokerConfig
    holder: list = []
    _install_fake_futu(monkeypatch, last_trd_ctx_holder=holder)
    b = FutuBroker(FutuBrokerConfig(
        host="127.0.0.1", port=11111, market="US", execution_env="futu_sim",
    ))
    b.connect()
    intent = make_intent(
        strategy_id="phase2", symbol="US.AAPL", market="US", side="BUY",
        qty=10, price=100.0, rebalance_date="2026-05-20",
    )
    b.place_order(intent)
    remark = holder[-1].place_calls[0]["remark"]
    assert len(remark) <= 32
    # request_id starts with 'p2live-'
    assert remark.startswith("p2live-")
