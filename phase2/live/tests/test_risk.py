"""Unit tests for phase2.live.risk.

Mirrors the original classic_multifactor risk semantics while verifying the
phase2-specific adaptations:
- Pure phase2 imports only (no scripts.classic_multifactor.*).
- size_and_check is safely re-entrant (no shared LiveRiskGuard._seen).
- Edge cases: qty_zero, invalid_price, invalid_side, max_order_value cap.
"""

from __future__ import annotations

import sys

import pytest

from phase2.live.risk import (
    ClassicOrderRiskManager,
    Phase2RiskConfig,
    RebalanceContext,
    RiskDecision,
)


@pytest.fixture
def cfg() -> Phase2RiskConfig:
    return Phase2RiskConfig(max_position_pct=0.20, max_order_value=20_000.0)


@pytest.fixture
def mgr(cfg: Phase2RiskConfig) -> ClassicOrderRiskManager:
    return ClassicOrderRiskManager(cfg, market="us")


def _ctx() -> RebalanceContext:
    return RebalanceContext(datetime="2026-05-20", reason="phase2_test")


# ---------------------------------------------------------------------------
# Buy-side sizing
# ---------------------------------------------------------------------------

class TestBuySizing:
    def test_full_qty_within_caps(self, mgr):
        d = mgr.size_and_check(
            symbol="US.NVDA", side="BUY", price=100.0, cash=20_000, equity=100_000,
            current_qty=0, target_qty=100, factor=_ctx(),
        )
        # cap: max_order_value=20000, equity*0.20=20000, cash=20000 -> all 20000
        # qty allowed = 20000 // 100 = 200, requested 100, so qty=100
        assert d.allowed is True
        assert d.reasons == []
        assert d.qty == 100
        assert d.notional == pytest.approx(10_000.0)

    def test_max_order_value_cap(self, mgr):
        d = mgr.size_and_check(
            symbol="US.AAPL", side="BUY", price=100.0, cash=1_000_000, equity=1_000_000,
            current_qty=0, target_qty=10_000, factor=_ctx(),
        )
        # cap by max_order_value=20000 -> qty <= 200
        assert d.qty == 200
        assert d.notional == pytest.approx(20_000.0)

    def test_max_position_pct_cap(self, mgr):
        # equity 1_000_000 * 0.2 = 200_000 cap, BUT max_order_value (20000) is the binding cap
        d = mgr.size_and_check(
            symbol="US.AAPL", side="BUY", price=10.0, cash=1_000_000, equity=1_000_000,
            current_qty=0, target_qty=10_000, factor=_ctx(),
        )
        assert d.qty == 2_000  # 20000 // 10
        assert d.allowed is True

    def test_low_cash_caps_qty(self, mgr):
        d = mgr.size_and_check(
            symbol="US.AAPL", side="BUY", price=50.0, cash=500.0, equity=10_000,
            current_qty=0, target_qty=100, factor=_ctx(),
        )
        # cap by cash 500 -> 500 // 50 = 10
        assert d.qty == 10

    def test_zero_target_zero_qty_rejected(self, mgr):
        d = mgr.size_and_check(
            symbol="US.AAPL", side="BUY", price=100.0, cash=1_000_000, equity=1_000_000,
            current_qty=0, target_qty=0, factor=_ctx(),
        )
        assert d.allowed is False
        assert "qty_zero" in d.reasons


# ---------------------------------------------------------------------------
# Sell-side sizing
# ---------------------------------------------------------------------------

class TestSellSizing:
    def test_sell_capped_by_current_position(self, mgr):
        d = mgr.size_and_check(
            symbol="US.AAPL", side="SELL", price=100.0, cash=0, equity=10_000,
            current_qty=50, target_qty=0, factor=_ctx(),
        )
        assert d.qty == 50

    def test_sell_more_than_position_capped(self, mgr):
        d = mgr.size_and_check(
            symbol="US.AAPL", side="SELL", price=100.0, cash=0, equity=10_000,
            current_qty=50, target_qty=-200, factor=_ctx(),
        )
        assert d.qty == 50

    def test_sell_with_no_position_qty_zero(self, mgr):
        d = mgr.size_and_check(
            symbol="US.AAPL", side="SELL", price=100.0, cash=0, equity=10_000,
            current_qty=0, target_qty=-50, factor=_ctx(),
        )
        assert d.allowed is False
        assert "qty_zero" in d.reasons


# ---------------------------------------------------------------------------
# Invalid inputs
# ---------------------------------------------------------------------------

class TestInvalid:
    def test_invalid_price(self, mgr):
        d = mgr.size_and_check(
            symbol="US.AAPL", side="BUY", price=0.0, cash=1_000, equity=10_000,
            current_qty=0, target_qty=10, factor=_ctx(),
        )
        assert d.allowed is False
        assert "invalid_price" in d.reasons

    def test_invalid_side(self, mgr):
        d = mgr.size_and_check(
            symbol="US.AAPL", side="HOLD", price=100.0, cash=1_000, equity=10_000,
            current_qty=0, target_qty=10, factor=_ctx(),
        )
        assert d.allowed is False
        assert "invalid_side" in d.reasons

    def test_factor_none_uses_default_reason(self, mgr):
        d = mgr.size_and_check(
            symbol="US.AAPL", side="BUY", price=100.0, cash=1_000, equity=10_000,
            current_qty=0, target_qty=5, factor=None,
        )
        assert isinstance(d, RiskDecision)


# ---------------------------------------------------------------------------
# Re-entrancy / idempotency adaptation (phase2 deviation)
# ---------------------------------------------------------------------------

class TestReentrancy:
    def test_double_size_and_check_not_blocked(self, mgr):
        d1 = mgr.size_and_check(
            symbol="US.AAPL", side="BUY", price=100.0, cash=1_000_000, equity=1_000_000,
            current_qty=0, target_qty=100, factor=_ctx(),
        )
        d2 = mgr.size_and_check(
            symbol="US.AAPL", side="BUY", price=100.0, cash=1_000_000, equity=1_000_000,
            current_qty=0, target_qty=100, factor=_ctx(),
        )
        # Same request_id but BOTH must be allowed; idempotency belongs to a
        # separate gate, not the risk manager.
        assert d1.request_id == d2.request_id
        assert d1.allowed is True
        assert d2.allowed is True


# ---------------------------------------------------------------------------
# No scripts.classic_multifactor import leak
# ---------------------------------------------------------------------------

def test_no_classic_multifactor_import():
    """phase2.live.risk must be importable without scripts.classic_multifactor
    having been imported before. We detect leakage by purging then reimporting.
    """
    # Purge any prior phase2.live.risk import.
    for mod in list(sys.modules):
        if mod == "phase2.live.risk":
            del sys.modules[mod]
    # Whether scripts.classic_multifactor was already imported by an unrelated
    # test is fine; what we care about is that phase2.live.risk's *own* code
    # doesn't pull it in. Take a snapshot before import.
    cm_before = "scripts.classic_multifactor" in sys.modules or any(
        k.startswith("scripts.classic_multifactor.") for k in sys.modules
    )
    import phase2.live.risk  # noqa: F401

    cm_after = "scripts.classic_multifactor" in sys.modules or any(
        k.startswith("scripts.classic_multifactor.") for k in sys.modules
    )
    # Re-importing phase2.live.risk must not introduce a new classic_multifactor
    # module entry.
    assert cm_after == cm_before


# ---------------------------------------------------------------------------
# RiskDecision shape
# ---------------------------------------------------------------------------

def test_risk_decision_to_dict_round_trip(mgr):
    d = mgr.size_and_check(
        symbol="US.AAPL", side="BUY", price=100.0, cash=1_000, equity=10_000,
        current_qty=0, target_qty=5, factor=_ctx(),
    )
    payload = d.to_dict()
    assert set(payload.keys()) == {
        "allowed", "reasons", "qty", "notional", "target_position_pct", "request_id",
    }
