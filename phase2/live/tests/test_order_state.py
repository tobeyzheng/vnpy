"""Unit tests for phase2.live.order_state.

Covers:
- build_request_id: determinism, validation
- transition: legal/illegal transitions, terminal states
- OrderStateStoreExt: save_intent_as_state idempotency, list_open_request_ids,
  update_status, restart-recovery scenario.
"""

from __future__ import annotations

import pytest

from phase2.live.order_state import (
    LEGAL_TRANSITIONS,
    OPEN_STATUSES,
    TERMINAL_STATUSES,
    OrderIntent,
    OrderState,
    OrderStateStoreExt,
    build_request_id,
    make_intent,
    transition,
)


# ---------------------------------------------------------------------------
# build_request_id
# ---------------------------------------------------------------------------

class TestBuildRequestId:
    def test_deterministic_same_inputs(self):
        a = build_request_id(
            strategy_id="phase2_us_multi", symbol="US.NVDA", side="BUY",
            rebalance_date="2026-05-20", seq=0,
        )
        b = build_request_id(
            strategy_id="phase2_us_multi", symbol="US.NVDA", side="BUY",
            rebalance_date="2026-05-20", seq=0,
        )
        assert a == b
        assert a.startswith("p2live-")
        assert len(a) == len("p2live-") + 16

    def test_seq_changes_id(self):
        a = build_request_id(
            strategy_id="s", symbol="US.AAPL", side="BUY",
            rebalance_date="2026-05-20", seq=0,
        )
        b = build_request_id(
            strategy_id="s", symbol="US.AAPL", side="BUY",
            rebalance_date="2026-05-20", seq=1,
        )
        assert a != b

    def test_side_normalised(self):
        a = build_request_id(
            strategy_id="s", symbol="US.AAPL", side="buy",
            rebalance_date="2026-05-20",
        )
        b = build_request_id(
            strategy_id="s", symbol="US.AAPL", side="BUY",
            rebalance_date="2026-05-20",
        )
        assert a == b

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"strategy_id": "", "symbol": "US.AAPL", "side": "BUY", "rebalance_date": "d"},
            {"strategy_id": "s", "symbol": "", "side": "BUY", "rebalance_date": "d"},
            {"strategy_id": "s", "symbol": "US.AAPL", "side": "HOLD", "rebalance_date": "d"},
            {"strategy_id": "s", "symbol": "US.AAPL", "side": "BUY", "rebalance_date": ""},
        ],
    )
    def test_invalid_inputs(self, kwargs):
        with pytest.raises(ValueError):
            build_request_id(**kwargs)

    def test_negative_seq_rejected(self):
        with pytest.raises(ValueError):
            build_request_id(
                strategy_id="s", symbol="US.AAPL", side="BUY",
                rebalance_date="2026-05-20", seq=-1,
            )


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------

def _state(status: str = "created", request_id: str = "rq-1") -> OrderState:
    return OrderState(
        request_id=request_id, symbol="US.AAPL", market="US",
        side="BUY", qty=10, status=status, price=100.0, strategy_id="s",
    )


class TestTransition:
    def test_happy_path_to_filled(self):
        s = _state("created")
        for tgt in ("validated", "risk_checked", "approved", "submitting", "submitted", "filled"):
            s = transition(s, tgt)
        assert s.status == "filled"

    def test_reject_from_created_allowed(self):
        s = transition(_state("created"), "rejected")
        assert s.status == "rejected"

    def test_terminal_rejected_cannot_transition(self):
        with pytest.raises(ValueError):
            transition(_state("rejected"), "submitted")

    def test_terminal_filled_can_only_reconcile(self):
        ok = transition(_state("filled"), "reconciled")
        assert ok.status == "reconciled"
        with pytest.raises(ValueError):
            transition(_state("filled"), "cancelled")

    def test_unknown_target_rejected(self):
        with pytest.raises(ValueError):
            transition(_state("created"), "wat")

    def test_open_terminal_partition(self):
        # All keys of LEGAL_TRANSITIONS should be partitioned into open or terminal.
        all_known = set(LEGAL_TRANSITIONS.keys())
        # 'approval_required' / 'submitting' are open, not terminal.
        assert OPEN_STATUSES.issubset(all_known)
        assert TERMINAL_STATUSES.issubset(all_known)
        assert OPEN_STATUSES.isdisjoint(TERMINAL_STATUSES)

    def test_does_not_mutate_input(self):
        s = _state("created")
        s2 = transition(s, "validated")
        assert s.status == "created"
        assert s2.status == "validated"
        assert s is not s2


# ---------------------------------------------------------------------------
# OrderStateStoreExt
# ---------------------------------------------------------------------------

class TestOrderStateStoreExt:
    def test_save_intent_idempotent(self, tmp_path):
        store = OrderStateStoreExt(tmp_path / "orders")
        intent = make_intent(
            strategy_id="phase2", symbol="US.NVDA", market="US", side="BUY",
            qty=10, price=120.0, rebalance_date="2026-05-20",
            execution_env="futu_sim",
        )
        s1 = store.save_intent_as_state(intent)
        # Promote to a non-initial status, then re-save the same intent: must
        # NOT clobber the existing state back to 'created'.
        store.update_status(intent.request_id, "validated")
        s2 = store.save_intent_as_state(intent)
        assert s2.status == "validated"
        assert s1.request_id == s2.request_id

    def test_list_open_request_ids(self, tmp_path):
        store = OrderStateStoreExt(tmp_path / "orders")
        # Two open + one terminal.
        for sym, status in (("US.AAPL", "submitted"), ("US.NVDA", "approved"), ("US.MSFT", "filled")):
            intent = make_intent(
                strategy_id="phase2", symbol=sym, market="US", side="BUY",
                qty=1, price=10.0, rebalance_date="2026-05-20",
            )
            store.save_intent_as_state(intent)
            # Drive each one to its target status via legal transitions.
            if status == "submitted":
                for tgt in ("validated", "risk_checked", "approved", "submitting", "submitted"):
                    store.update_status(intent.request_id, tgt)
            elif status == "approved":
                for tgt in ("validated", "risk_checked", "approved"):
                    store.update_status(intent.request_id, tgt)
            elif status == "filled":
                for tgt in ("validated", "risk_checked", "approved", "submitting", "submitted", "filled"):
                    store.update_status(intent.request_id, tgt)

        open_ids = store.list_open_request_ids()
        assert len(open_ids) == 2
        # Re-instantiate the store to simulate a process restart.
        store2 = OrderStateStoreExt(tmp_path / "orders")
        assert sorted(store2.list_open_request_ids()) == sorted(open_ids)
        assert store2.summary()["open"] == 2
        assert store2.summary()["filled"] == 1

    def test_update_status_unknown_request_id(self, tmp_path):
        store = OrderStateStoreExt(tmp_path / "orders")
        with pytest.raises(KeyError):
            store.update_status("missing", "validated")

    def test_update_status_illegal(self, tmp_path):
        store = OrderStateStoreExt(tmp_path / "orders")
        intent = make_intent(
            strategy_id="phase2", symbol="US.AAPL", market="US", side="BUY",
            qty=1, price=10.0, rebalance_date="2026-05-20",
        )
        store.save_intent_as_state(intent)
        with pytest.raises(ValueError):
            store.update_status(intent.request_id, "filled")

    def test_save_intent_with_existing_request_id(self, tmp_path):
        """Same deterministic request_id from two intent factories must coalesce."""
        store = OrderStateStoreExt(tmp_path / "orders")
        i1 = make_intent(
            strategy_id="phase2", symbol="US.AAPL", market="US", side="BUY",
            qty=10, price=100.0, rebalance_date="2026-05-20",
        )
        i2 = make_intent(
            strategy_id="phase2", symbol="US.AAPL", market="US", side="BUY",
            qty=20, price=99.0, rebalance_date="2026-05-20",  # qty/price differ but seq=0
        )
        assert i1.request_id == i2.request_id
        s1 = store.save_intent_as_state(i1)
        s2 = store.save_intent_as_state(i2)
        # Second save sees existing record and returns it untouched (qty=10).
        assert s2.qty == 10
        assert s2.status == s1.status == "created"

    def test_intent_carries_execution_env(self):
        intent = make_intent(
            strategy_id="phase2", symbol="US.AAPL", market="US", side="BUY",
            qty=1, price=10.0, rebalance_date="2026-05-20",
            execution_env="futu_real",
        )
        assert intent.execution_env == "futu_real"
        assert intent.execution_channel == "futu"
        assert intent.source_phase == "live_session"


# ---------------------------------------------------------------------------
# OrderIntent / OrderState (smoke import)
# ---------------------------------------------------------------------------

def test_imports_are_aliases_to_services_models():
    """Ensure phase2.live re-exports the same dataclass identity used by services."""
    from services.common.trading_models import OrderIntent as SvcIntent
    from services.common.trading_models import OrderState as SvcState

    assert OrderIntent is SvcIntent
    assert OrderState is SvcState
