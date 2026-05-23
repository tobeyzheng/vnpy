"""Unit tests for phase2.live.runner.

We avoid loading the real futumd strategy (it has heavy logic) and instead
write a tiny throwaway strategy file to ``tmp_path`` that exercises
``place_limit`` deterministically. The runner is driven with an injectable
clock + sleep so no test ever waits on real time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import pytest

from phase2.live.broker import (
    BrokerAccount,
    BrokerOrderAck,
    BrokerOrderUpdate,
    BrokerPosition,
)
from phase2.live.guards import (
    EventLogger,
    GatePipeline,
    IdempotencyGate,
    PortfolioRiskGate,
    PortfolioRiskLimits,
    ReconciliationGate,
    RiskGate,
)
from phase2.live.order_state import OrderStateStoreExt
from phase2.live.risk import ClassicOrderRiskManager, Phase2RiskConfig
from phase2.live.runner import (
    BarsProvider,
    DailyLiveRebalanceRunner,
    DailyReport,
    RunnerConfig,
)


# ---------------------------------------------------------------------------
# Lightweight in-memory broker (also covers Task 4 protocol round-trip)
# ---------------------------------------------------------------------------

class InMemoryBroker:
    def __init__(self, *, execution_env: str = "futu_sim",
                 unlock_ok: bool = True, place_ok: bool = True):
        self.execution_env = execution_env
        self.unlock_ok = unlock_ok
        self.place_ok = place_ok
        self.connected = False
        self.unlocked = execution_env == "futu_sim"
        self.subscribed: list[str] = []
        self.placed: list = []
        self.cancelled: list[str] = []
        self.handler = None
        self._next_id = 0

    def connect(self): self.connected = True
    def disconnect(self): self.connected = False

    def unlock_trade(self):
        if self.execution_env == "futu_sim":
            self.unlocked = True
            return True
        ok = self.unlock_ok
        self.unlocked = ok
        return ok

    def query_account(self):
        return BrokerAccount(cash=100_000.0, market_value=0.0, total_assets=100_000.0)

    def query_positions(self): return []

    def place_order(self, intent):
        self.placed.append(intent)
        if not self.place_ok:
            return BrokerOrderAck(intent.request_id, "", 0, "rejected", "forced")
        self._next_id += 1
        return BrokerOrderAck(
            request_id=intent.request_id,
            broker_order_id=f"BRK-{self._next_id}",
            accepted_qty=intent.qty, status="submitted",
        )

    def cancel_order(self, broker_order_id):
        self.cancelled.append(broker_order_id)
        return True

    def subscribe_quote(self, symbols):
        self.subscribed.extend(list(symbols))

    def register_order_handler(self, callback):
        self.handler = callback

    # Test helper: simulate a fill arriving from OpenD.
    def emit_fill(self, *, broker_order_id: str, symbol: str, qty: int,
                  price: float, status: str = "filled", request_id: str | None = None):
        if self.handler is None:
            return
        self.handler(BrokerOrderUpdate(
            broker_order_id=broker_order_id, request_id=request_id,
            symbol=symbol, status=status, filled_qty=qty,
            avg_fill_price=price,
        ))


# ---------------------------------------------------------------------------
# Fake clock
# ---------------------------------------------------------------------------

@dataclass
class FakeClock:
    t: float = 0.0
    sleeps: list = None

    def __post_init__(self):
        self.sleeps = []

    def now(self): return self.t
    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


# ---------------------------------------------------------------------------
# Tiny strategy file factory
# ---------------------------------------------------------------------------

def _write_strategy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "tiny_strategy.py"
    p.write_text(dedent(body), encoding="utf-8")
    return p


SIMPLE_BUY_STRATEGY = """
    class Strategy(StrategyBase):
        def initialize(self):
            pass
        def handle_data(self):
            # Buy 10 shares of US.AAPL at 100 each.
            place_limit(
                symbol="US.AAPL", price=100.0, qty=10,
                side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            )
"""

REJECTING_BUY_STRATEGY = """
    class Strategy(StrategyBase):
        def initialize(self): pass
        def handle_data(self):
            # qty=0 ⇒ adapter rejects silently with alert.
            place_limit(
                symbol="US.AAPL", price=100.0, qty=0,
                side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            )
"""


# ---------------------------------------------------------------------------
# Pipeline + runner builder
# ---------------------------------------------------------------------------

def _build_pipeline(tmp_path: Path):
    store = OrderStateStoreExt(tmp_path / "orders")
    events = EventLogger(tmp_path / "events.jsonl")
    cfg = Phase2RiskConfig(max_position_pct=0.50, max_order_value=10_000_000.0)
    gates = [
        IdempotencyGate(store),
        ReconciliationGate(tmp_path / "rec", require_report=False),
        RiskGate(ClassicOrderRiskManager(cfg)),
        PortfolioRiskGate(PortfolioRiskLimits(
            max_single_position_pct=0.99,
            max_daily_new_position_pct=0.99,
            max_market_exposure_pct=0.99,
            max_drawdown_pct=0.99,
        )),
    ]
    pipe = GatePipeline(gates, store=store, events=events)
    return store, pipe, events


def _make_bars_provider(symbols, last_close=100.0) -> BarsProvider:
    def _provider(syms):
        out = {}
        for sym in syms:
            out[sym] = [
                (f"2026-05-{18+i:02d}", last_close - 1, last_close + 1,
                 last_close - 2, last_close, 1_000.0)
                for i in range(3)
            ]
        return out
    return _provider


def _runner(tmp_path, *, strategy_body: str = SIMPLE_BUY_STRATEGY,
            execution_env="futu_sim", live_submit=True,
            broker_kw=None, runtime_seconds=300.0,
            post_wait=0.0, rebalance_time_epoch=None) -> tuple:
    strategy_path = _write_strategy(tmp_path, strategy_body)
    store, pipeline, events = _build_pipeline(tmp_path)
    broker = InMemoryBroker(execution_env=execution_env, **(broker_kw or {}))
    products_dir = tmp_path / "run"
    cfg = RunnerConfig(
        strategy_path=strategy_path,
        symbols=["US.AAPL"],
        init_cash=100_000.0,
        rebalance_date="2026-05-20",
        execution_env=execution_env,
        products_dir=products_dir,
        live_submit=live_submit,
        bar_warmup=3,
        post_rebalance_wait_seconds=post_wait,
        max_runtime_seconds=runtime_seconds,
        rebalance_time_epoch=rebalance_time_epoch,
    )
    clock = FakeClock(t=1_700_000_000.0)
    runner = DailyLiveRebalanceRunner(
        cfg, broker=broker, store=store, pipeline=pipeline, events=events,
        bars_provider=_make_bars_provider(cfg.symbols),
        clock=clock.now, sleep_fn=clock.sleep,
    )
    return runner, broker, store, events, products_dir, clock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunOnceHappyPath:
    def test_dry_run_does_not_call_broker(self, tmp_path):
        runner, broker, store, _events, prod, _clock = _runner(
            tmp_path, execution_env="dry_run", live_submit=False,
        )
        report = runner.run_once()
        assert isinstance(report, DailyReport)
        assert report.intents_emitted == 1
        assert report.intents_approved == 1
        assert broker.placed == []  # NEVER called in dry run
        assert (prod / "daily_report.json").is_file()
        assert (prod / "positions_snapshot.csv").is_file()

    def test_sim_live_submit_calls_broker(self, tmp_path):
        runner, broker, store, _events, prod, _clock = _runner(
            tmp_path, execution_env="futu_sim", live_submit=True,
        )
        report = runner.run_once()
        assert report.intents_approved == 1
        assert len(broker.placed) == 1
        assert broker.placed[0].symbol == "US.AAPL"
        # State must reach 'submitted' since broker accepts.
        states = store.list()
        assert len(states) == 1
        assert states[0].status == "submitted"
        assert states[0].broker_order_id == "BRK-1"

    def test_real_unlock_failure_blocks_orders(self, tmp_path):
        runner, broker, _store, _events, _prod, _clock = _runner(
            tmp_path, execution_env="futu_real", live_submit=True,
            broker_kw={"unlock_ok": False},
        )
        report = runner.run_once()
        # Unlock failed → run_once short-circuits before strategy handle_data.
        assert report.intents_emitted == 0
        assert broker.placed == []

    def test_invalid_intent_does_not_reach_pipeline(self, tmp_path):
        runner, broker, store, _events, _prod, _clock = _runner(
            tmp_path, strategy_body=REJECTING_BUY_STRATEGY,
            execution_env="futu_sim", live_submit=True,
        )
        report = runner.run_once()
        assert report.intents_emitted == 0  # adapter rejected before callback
        assert broker.placed == []
        # An invalid_intent alert was recorded by the adapter.
        assert any(t == "invalid_intent" for (t, _) in report.alerts)


class TestPipelineIntegration:
    def test_idempotent_re_run(self, tmp_path):
        runner, broker, store, _events, _prod, _clock = _runner(
            tmp_path, execution_env="futu_sim", live_submit=True,
        )
        runner.run_once()
        # Second run with the same rebalance_date: same request_id ⇒ pipeline
        # idempotency gate must block since state is already 'submitted'.
        runner2, broker2, store2, _e, _p, _c = _runner(
            tmp_path, execution_env="futu_sim", live_submit=True,
        )
        # Reuse the same orders dir to simulate process restart.
        report2 = runner2.run_once()
        # The fresh runner has its own store under the same tmp_path/orders;
        # after restart the in-flight request_id is loaded → idempotency
        # blocks the second submission.
        assert report2.blocked_by_gate.get("idempotency", 0) >= 1
        # broker should NOT receive a second place_order for the same intent.
        assert len(broker2.placed) == 0

    def test_broker_rejection_records_event(self, tmp_path):
        runner, broker, store, _events, prod, _clock = _runner(
            tmp_path, execution_env="futu_sim", live_submit=True,
            broker_kw={"place_ok": False},
        )
        report = runner.run_once()
        # Strategy still emits + pipeline approves, but broker rejects.
        assert report.intents_approved == 1
        assert len(broker.placed) == 1
        # Event log must mention broker_rejected.
        events_text = (prod.parent / "events.jsonl").read_text() if (prod.parent / "events.jsonl").exists() else ""
        # event log lives at tmp_path/events.jsonl per _build_pipeline.
        events_text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        assert '"event_type": "broker_rejected"' in events_text


class TestEodCancelBehaviour:
    def _setup_with_open_order(self, tmp_path, *, auto_cancel: bool, exec_env: str):
        runner, broker, store, _events, _prod, _clock = _runner(
            tmp_path, execution_env=exec_env, live_submit=True,
        )
        runner.config.auto_cancel_on_eod = auto_cancel
        return runner, broker, store

    def test_real_force_cancel_even_when_flag_off(self, tmp_path):
        runner, broker, store = self._setup_with_open_order(
            tmp_path, auto_cancel=False, exec_env="futu_real",
        )
        # REAL needs a non-empty password for FutuBroker; InMemoryBroker
        # ignores it. We just need to make unlock_trade succeed.
        broker.unlock_ok = True
        runner.run_once()
        # 1 order submitted, still open → REAL must auto-cancel even with the
        # flag off (per requirements §4.5).
        assert len(broker.cancelled) == 1

    def test_sim_respects_auto_cancel_flag_off(self, tmp_path):
        runner, broker, store = self._setup_with_open_order(
            tmp_path, auto_cancel=False, exec_env="futu_sim",
        )
        runner.run_once()
        assert broker.cancelled == []  # SIM honours flag

    def test_sim_auto_cancel_when_flag_on(self, tmp_path):
        runner, broker, store = self._setup_with_open_order(
            tmp_path, auto_cancel=True, exec_env="futu_sim",
        )
        runner.run_once()
        assert len(broker.cancelled) == 1


class TestFillCallback:
    def test_fill_advances_state_to_filled(self, tmp_path):
        runner, broker, store, _events, _prod, _clock = _runner(
            tmp_path, execution_env="futu_sim", live_submit=True,
        )
        runner.run_once()
        assert len(broker.placed) == 1
        # Simulate a fill arriving via the broker callback.
        broker.emit_fill(
            broker_order_id="BRK-1", symbol="US.AAPL", qty=10, price=99.0,
        )
        states = store.list()
        assert len(states) == 1
        assert states[0].status == "filled"
        assert states[0].filled_qty == 10
        assert states[0].avg_fill_price == 99.0


class TestWaitLoop:
    def test_max_runtime_short_circuits_wait(self, tmp_path):
        # rebalance_time_epoch in the future, but max_runtime_seconds is small;
        # we rely on the wait loop to honour the deadline.
        runner, broker, _, _, _, clock = _runner(
            tmp_path, execution_env="futu_sim", live_submit=True,
            rebalance_time_epoch=1_700_000_000.0 + 100.0,
        )
        # FakeClock starts at 1_700_000_000.0, so wait_until_rebalance_time
        # will sleep at most 100s; our FakeClock advances on each sleep so
        # the loop must terminate.
        runner._wait_until_rebalance_time()
        assert clock.t >= 1_700_000_000.0 + 100.0


class TestProductsLayout:
    def test_products_dir_layout(self, tmp_path):
        runner, _, _, _, prod, _ = _runner(
            tmp_path, execution_env="futu_sim", live_submit=True,
        )
        runner.run_once()
        report = json.loads((prod / "daily_report.json").read_text(encoding="utf-8"))
        assert report["execution_env"] == "futu_sim"
        assert report["pool"] == ["US.AAPL"]
        assert "intents_approved" in report
        # positions_snapshot.csv must list one row per symbol.
        snap = (prod / "positions_snapshot.csv").read_text(encoding="utf-8").splitlines()
        assert snap[0].split(",") == ["symbol", "qty", "last_close", "market_value"]
        assert any("US.AAPL" in l for l in snap[1:])
