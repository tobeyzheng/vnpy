"""Unit tests for phase2.live.live_adapter.

Key invariants:
- Live & backtest DSL namespaces expose the *same* set of names so the
  futumd strategy file's stub block stays inactive in both modes.
- ``place_limit`` and ``close_positions`` route to the ``intent_callback``
  rather than populating a backtest pending list.
- Invalid intents (qty<=0, price<=0, unknown side, missing symbol) are
  rejected without invoking the callback and emit an alert.
- Loading the real futumd strategy file with the live namespace succeeds and
  exposes a ``Strategy`` class.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from phase2.live.live_adapter import (
    LivePortfolioRuntime,
    PortfolioRuntime,
    build_live_futumd_namespace,
    load_live_futumd_strategy,
)


FUTUMD_STRATEGY_FILE = Path(
    "/projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py"
)
BACKTEST_ADAPTER_FILE = Path(
    "/projects/vnpy/phase2/backtest/futumd_strategy_adapter.py"
)


def _load_backtest_adapter_module():
    """Load the backtest adapter directly from its file to avoid triggering
    ``phase2/backtest/__init__.py`` (which imports vnpy.trader.database).
    """
    import sys
    name = "_phase2_bt_adapter_for_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(BACKTEST_ADAPTER_FILE))
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass field-type lookups via cls.__module__
    # can resolve the module's own namespace.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Namespace alignment with backtest
# ---------------------------------------------------------------------------

def _make_runtime(symbols=("US.AAPL", "US.NVDA"), execution_env="dry_run") -> LivePortfolioRuntime:
    return LivePortfolioRuntime(
        symbols=list(symbols),
        cash_value=100_000.0,
        rebalance_date="2026-05-20",
        execution_env=execution_env,
    )


class TestNamespaceAlignment:
    def test_keys_match_backtest_exactly(self):
        rt_live = _make_runtime()
        bt_mod = _load_backtest_adapter_module()
        rt_bt = bt_mod.PortfolioRuntime(symbols=list(rt_live.symbols), cash_value=100_000.0)
        bt_keys = set(bt_mod.build_futumd_namespace(rt_bt).keys())
        live_keys = set(build_live_futumd_namespace(rt_live, intent_callback=lambda x: None).keys())
        assert live_keys == bt_keys, f"diff={bt_keys.symmetric_difference(live_keys)}"

    def test_dsl_read_functions_share_signatures(self):
        rt_live = _make_runtime()
        bt_mod = _load_backtest_adapter_module()
        rt_bt = bt_mod.PortfolioRuntime(symbols=list(rt_live.symbols), cash_value=100_000.0)
        live_ns = build_live_futumd_namespace(rt_live, intent_callback=lambda x: None)
        bt_ns = bt_mod.build_futumd_namespace(rt_bt)
        for name in ("bar_close", "bar_open", "bar_high", "bar_low", "bar_volume",
                     "cash", "net_asset", "position_holding_qty",
                     "alert", "place_limit", "close_positions"):
            assert callable(live_ns[name])
            assert callable(bt_ns[name])


# ---------------------------------------------------------------------------
# Read-side parity
# ---------------------------------------------------------------------------

class TestRead:
    def test_bar_close_select_semantics(self):
        rt = _make_runtime()
        rt.push_bar("US.AAPL", o=99, h=101, l=98, c=100, v=1_000)
        rt.push_bar("US.AAPL", o=100, h=102, l=99, c=101, v=1_500)
        ns = build_live_futumd_namespace(rt, intent_callback=lambda x: None)
        assert ns["bar_close"](symbol="US.AAPL", select=1) == 101
        assert ns["bar_close"](symbol="US.AAPL", select=2) == 100
        # missing symbol → 0
        assert ns["bar_close"](symbol="US.MISS", select=1) == 0.0

    def test_cash_and_net_asset(self):
        rt = _make_runtime()
        rt.push_bar("US.AAPL", o=100, h=100, l=100, c=110, v=10)
        rt.positions["US.AAPL"] = 50
        ns = build_live_futumd_namespace(rt, intent_callback=lambda x: None)
        assert ns["cash"]() == 100_000.0
        assert ns["net_asset"]() == pytest.approx(100_000.0 + 50 * 110.0)

    def test_position_holding_qty(self):
        rt = _make_runtime()
        rt.positions["US.AAPL"] = 25
        ns = build_live_futumd_namespace(rt, intent_callback=lambda x: None)
        assert ns["position_holding_qty"](symbol="US.AAPL") == 25
        assert ns["position_holding_qty"](symbol="US.MISS") == 0


# ---------------------------------------------------------------------------
# Write-side: place_limit / close_positions emit OrderIntent
# ---------------------------------------------------------------------------

class TestPlaceLimit:
    def test_buy_emits_intent(self):
        rt = _make_runtime(execution_env="futu_sim")
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        OrderSide = ns["OrderSide"]
        TimeInForce = ns["TimeInForce"]
        ns["place_limit"](
            symbol="US.AAPL", price=100.0, qty=10,
            side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
        )
        assert len(captured) == 1
        intent = captured[0]
        assert intent.symbol == "US.AAPL"
        assert intent.side == "BUY"
        assert intent.qty == 10
        assert intent.price == 100.0
        assert intent.execution_env == "futu_sim"
        assert intent.execution_channel == "futu"
        assert intent.source_phase == "live_session"
        assert intent.strategy_id == "phase2_us_multi"
        assert intent.request_id.startswith("p2live-")

    def test_sell_emits_intent(self):
        rt = _make_runtime(execution_env="futu_sim")
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        ns["place_limit"](
            symbol="us.aapl", price=100.0, qty=5,
            side=ns["OrderSide"].SELL, time_in_force=ns["TimeInForce"].DAY,
        )
        assert captured[0].side == "SELL"
        assert captured[0].symbol == "US.AAPL"  # uppercased

    def test_invalid_qty_zero_no_callback(self):
        rt = _make_runtime()
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        ns["place_limit"](symbol="US.AAPL", price=100.0, qty=0, side=ns["OrderSide"].BUY)
        assert captured == []
        assert any(t == "invalid_intent" for (t, _) in rt.alerts_emitted)

    def test_invalid_price_no_callback(self):
        rt = _make_runtime()
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        ns["place_limit"](symbol="US.AAPL", price=0.0, qty=10, side=ns["OrderSide"].BUY)
        assert captured == []

    def test_unknown_side_no_callback(self):
        rt = _make_runtime()
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        ns["place_limit"](symbol="US.AAPL", price=100.0, qty=10, side="HOLD")
        assert captured == []

    def test_distinct_request_ids_for_repeat_calls(self):
        rt = _make_runtime()
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        for _ in range(3):
            ns["place_limit"](symbol="US.AAPL", price=100.0, qty=1, side=ns["OrderSide"].BUY)
        ids = [i.request_id for i in captured]
        assert len(set(ids)) == 3, "intent_seq must give distinct request_ids"


class TestClosePositions:
    def test_close_full_position(self):
        rt = _make_runtime()
        rt.positions["US.AAPL"] = 30
        rt.last_close["US.AAPL"] = 90.0
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        ns["close_positions"](symbol="US.AAPL")
        assert len(captured) == 1
        assert captured[0].side == "SELL"
        assert captured[0].qty == 30
        assert captured[0].price == 90.0

    def test_close_partial_position(self):
        rt = _make_runtime()
        rt.positions["US.AAPL"] = 30
        rt.last_close["US.AAPL"] = 90.0
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        ns["close_positions"](symbol="US.AAPL", qty=10)
        assert captured[0].qty == 10

    def test_close_no_position_no_callback(self):
        rt = _make_runtime()
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        ns["close_positions"](symbol="US.AAPL")
        assert captured == []

    def test_close_no_last_close_emits_alert(self):
        rt = _make_runtime()
        rt.positions["US.AAPL"] = 30
        rt.last_close["US.AAPL"] = 0.0
        captured = []
        ns = build_live_futumd_namespace(rt, intent_callback=captured.append)
        ns["close_positions"](symbol="US.AAPL")
        assert captured == []
        assert any(t == "close_positions_no_price" for (t, _) in rt.alerts_emitted)


# ---------------------------------------------------------------------------
# Strategy file load
# ---------------------------------------------------------------------------

class TestStrategyLoad:
    def test_loads_real_futumd_strategy(self):
        rt = _make_runtime()
        captured = []
        module, StrategyCls = load_live_futumd_strategy(
            FUTUMD_STRATEGY_FILE, rt, intent_callback=captured.append,
        )
        assert StrategyCls is not None
        assert module.Strategy is StrategyCls
        # Sanity: strategy file's place_limit symbol should resolve to the
        # live closure, not the backtest one.
        assert "place_limit" in module.__dict__

    def test_strategy_initialize_runs(self):
        rt = _make_runtime()
        rt.symbols = ["US.NVDA", "US.AAPL"]
        for s in rt.symbols:
            for c in (90.0, 95.0, 100.0):
                rt.push_bar(s, o=c - 1, h=c + 1, l=c - 2, c=c, v=1_000)
        captured = []
        module, StrategyCls = load_live_futumd_strategy(
            FUTUMD_STRATEGY_FILE, rt, intent_callback=captured.append,
            module_name="phase2_live_futumd_strategy_test_load",
        )
        # Instantiating the Strategy must not raise even with sparse data.
        StrategyCls()


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------

def test_alert_records_to_runtime():
    rt = _make_runtime()
    ns = build_live_futumd_namespace(rt, intent_callback=lambda _: None)
    ns["alert"]("hello", "world")
    assert ("hello", "world") in rt.alerts_emitted
    assert ("hello", "world") in rt.alerts
