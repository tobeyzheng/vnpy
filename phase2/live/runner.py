"""DailyLiveRebalanceRunner — single-trigger daily live trading orchestrator.

This runner is a *thin* event loop that wires together the existing phase2
live components:

    bars_provider  ─►  LivePortfolioRuntime  ─►  futumd Strategy.handle_data
                                              │
                                              ▼
                                         intent_callback
                                              │
                                              ▼
                                       GatePipeline (4 gates)
                                              │
                          (approved)─────────┘
                                              ▼
                                     LiveBroker.place_order
                                              │
                                              ▼
                            OrderStateStoreExt.update_status
                                              ▼
                              register_order_handler → fills

Design tenets:
- All clock / sleep / I/O is injectable so unit tests don't sleep on real
  time and don't touch the network.
- ``run_once()`` is the single-cycle path used by daily rebalance; the
  ``run()`` method wraps it with the optional rebalance-time wait loop and
  signal handlers.
- The runner *never* mutates ``phase2.backtest`` or ``phase2.strategy``; the
  futumd strategy file is loaded via ``load_live_futumd_strategy`` only.
"""

from __future__ import annotations

import csv
import json
import logging
import signal
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from phase2.live.broker import (
    BrokerOrderUpdate,
    LiveBroker,
)
from phase2.live.guards import (
    EventLogger,
    GateContext,
    GatePipeline,
    PortfolioState,
)
from phase2.live.live_adapter import (
    LivePortfolioRuntime,
    load_live_futumd_strategy,
)
from phase2.live.order_state import (
    OPEN_STATUSES,
    OrderIntent,
    OrderStateStoreExt,
    transition,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RunnerConfig",
    "DailyLiveRebalanceRunner",
    "DailyReport",
    "BarsProvider",
]


# A bars_provider returns: { symbol -> list[ (datetime_str, o, h, l, c, v) ] }
BarsProvider = Callable[[Iterable[str]], dict[str, list[tuple[str, float, float, float, float, float]]]]


@dataclass
class RunnerConfig:
    strategy_path: Path
    symbols: list[str]
    init_cash: float
    rebalance_date: str  # "YYYY-MM-DD"
    execution_env: str  # "dry_run" | "futu_sim" | "futu_real"
    products_dir: Path
    strategy_id: str = "phase2_us_multi"
    live_submit: bool = False
    auto_cancel_on_eod: bool = True
    bar_warmup: int = 60
    post_rebalance_wait_seconds: float = 120.0
    max_runtime_seconds: float = 1800.0
    rebalance_time_epoch: float | None = None  # absolute deadline; None = run immediately

    # Portfolio risk caps that the runner forwards into PortfolioState.
    market_existing_pct_initial: float = 0.0
    daily_new_pct_initial: float = 0.0
    current_drawdown_pct_initial: float = 0.0
    # When set, overrides the strategy module's ``LIVE_SUBMIT`` flag at
    # runtime so the strategy emits intents even though ``live_submit`` (the
    # broker switch) remains False. Used by the dry_run smoke link to walk
    # intent → gates → state without ever contacting OpenD.
    strategy_live_submit_override: bool | None = None


@dataclass
class DailyReport:
    pool: list[str]
    rebalance_date: str
    rebalance_time: str
    execution_env: str
    trade_count: int = 0
    fill_count: int = 0
    cancel_count: int = 0
    blocked_by_gate: dict[str, int] = field(default_factory=dict)
    final_nav: float = 0.0
    final_cash: float = 0.0
    total_return_pct: float = 0.0
    connection_drops: int = 0
    reconcile_breaches: int = 0
    alerts: list[tuple[str, str]] = field(default_factory=list)
    intents_emitted: int = 0
    intents_approved: int = 0


class DailyLiveRebalanceRunner:
    """Single-trigger daily rebalance runner.

    Parameters
    ----------
    config            : RunnerConfig (strategy file, pool, caps, etc.).
    broker            : an object satisfying ``LiveBroker``.
    store             : ``OrderStateStoreExt`` for persistence.
    pipeline          : ``GatePipeline`` already configured with 4 gates.
    events            : ``EventLogger`` (typically the same one the pipeline uses).
    bars_provider     : callable that returns the warmup + EOD bars per symbol.
    clock             : injectable wall-clock (defaults to time.time).
    sleep_fn          : injectable sleep (defaults to time.sleep).
    """

    HEARTBEAT_SECONDS = 600.0

    def __init__(
        self,
        config: RunnerConfig,
        *,
        broker: LiveBroker,
        store: OrderStateStoreExt,
        pipeline: GatePipeline,
        events: EventLogger,
        bars_provider: BarsProvider,
        clock: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.broker = broker
        self.store = store
        self.pipeline = pipeline
        self.events = events
        self.bars_provider = bars_provider
        self._clock = clock
        self._sleep = sleep_fn

        # Live runtime mirrors PortfolioState; we materialise it in run_once().
        self.runtime: LivePortfolioRuntime | None = None

        # Counters used to populate the daily report.
        self._intents_emitted: int = 0
        self._intents_approved: int = 0
        self._blocked_by_gate: dict[str, int] = {}
        self._broker_order_ids: list[str] = []
        self._fill_count: int = 0
        self._cancel_count: int = 0
        self._stopping: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> DailyReport:
        """Wait until rebalance_time, then run a single rebalance and exit."""
        self._install_signal_handlers()
        self._wait_until_rebalance_time()
        if self._stopping:
            return self._build_empty_report("graceful_shutdown_before_rebalance")
        return self.run_once()

    def run_once(self) -> DailyReport:
        """Execute one rebalance cycle end-to-end."""
        cfg = self.config
        run_started = self._clock()
        self._init_runtime()
        runtime = self.runtime
        assert runtime is not None
        # Wire intent callback → pipeline → broker.
        intent_handler = self._build_intent_handler()
        # Connect broker, install order handler.
        self.broker.connect()
        self.broker.register_order_handler(self._on_order_update)
        # Subscribe to quotes (no-op in dry_run / InMemoryBroker).
        try:
            self.broker.subscribe_quote(cfg.symbols)
        except Exception as exc:  # pragma: no cover - defensive
            self.events.emit("subscribe_failed", error=str(exc))
        # SIM: unlock_trade is a no-op; REAL: must succeed before any place_order.
        if not self.broker.unlock_trade():
            self.events.emit("unlock_failed", execution_env=cfg.execution_env)
            return self._finalize(run_started)

        # Load strategy file with live DSL bound to runtime + intent_handler.
        try:
            module, StrategyCls = load_live_futumd_strategy(
                cfg.strategy_path, runtime, intent_callback=intent_handler,
                module_name=f"phase2_live_strategy_{cfg.rebalance_date}",
            )
        except Exception as exc:
            self.events.emit("strategy_load_failed", error=str(exc))
            return self._finalize(run_started)

        # Push warmup + EOD bars.
        bars_by_symbol = self.bars_provider(cfg.symbols)
        for sym in cfg.symbols:
            for (_, o, h, l, c, v) in bars_by_symbol.get(sym, []):
                runtime.push_bar(sym, o, h, l, c, v)
            runtime.bar_index += 1

        # Override LIVE_SUBMIT in the strategy module. Default behaviour:
        # mirror the broker submission switch (``cfg.live_submit``). The CLI
        # may explicitly set ``strategy_live_submit_override=True`` so dry_run
        # smoke can exercise the full intent→gate path without ever calling
        # the broker (the broker call is still gated by ``cfg.live_submit``
        # in ``_build_intent_handler``).
        if hasattr(module, "LIVE_SUBMIT"):
            override = cfg.strategy_live_submit_override
            module.LIVE_SUBMIT = bool(cfg.live_submit) if override is None else bool(override)

        # Run a single rebalance pass: instantiate Strategy and call
        # initialize() if available, then handle_data().
        try:
            strategy = StrategyCls()
            if hasattr(strategy, "initialize"):
                try:
                    strategy.initialize()
                except Exception as exc:
                    self.events.emit("strategy_initialize_error", error=str(exc))
            if hasattr(strategy, "handle_data"):
                try:
                    strategy.handle_data()
                except Exception as exc:
                    self.events.emit("strategy_handle_data_error", error=str(exc))
        except Exception as exc:
            self.events.emit("strategy_construction_error", error=str(exc))

        # Wait for fills / cancellations within the post-rebalance window.
        self._wait_for_fills_and_cancels()

        # End of day: cancel all open orders if configured.
        if cfg.auto_cancel_on_eod or cfg.execution_env == "futu_real":
            self._cancel_all_open()

        return self._finalize(run_started)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _init_runtime(self) -> None:
        cfg = self.config
        self.runtime = LivePortfolioRuntime(
            symbols=list(cfg.symbols),
            cash_value=float(cfg.init_cash),
            strategy_id=cfg.strategy_id,
            rebalance_date=cfg.rebalance_date,
            execution_env=cfg.execution_env,
        )

    def _build_intent_handler(self) -> Callable[[OrderIntent], None]:
        """Build the closure that the live adapter calls on each place_limit.

        Order of operations on each intent:
        1. Bump runtime counter for daily report.
        2. Run the 4-stage gate pipeline.
        3. If approved & live_submit: call broker.place_order, advance state.
        4. If approved & not live_submit: keep state at 'approved' (dry run).
        5. If blocked: pipeline already wrote events.jsonl + state→rejected.
        """
        cfg = self.config

        def handler(intent: OrderIntent) -> None:
            self._intents_emitted += 1
            ctx = self._build_gate_context()
            decision = self.pipeline.run(intent, ctx)
            if not decision.allowed:
                gate = decision.blocked_by or "unknown"
                self._blocked_by_gate[gate] = self._blocked_by_gate.get(gate, 0) + 1
                return
            self._intents_approved += 1
            if not cfg.live_submit:
                # dry run: do not call broker.
                self.events.emit(
                    "dry_run", request_id=intent.request_id,
                    symbol=intent.symbol, side=intent.side, qty=intent.qty,
                )
                return
            # Submit to broker.
            try:
                state = self.store.load(intent.request_id)
                if state is not None and state.status == "approved":
                    self.store.update_status(intent.request_id, "submitting")
                ack = self.broker.place_order(intent)
                if ack.status == "rejected":
                    self.events.emit(
                        "broker_rejected",
                        request_id=intent.request_id,
                        message=ack.message,
                    )
                    s = self.store.load(intent.request_id)
                    if s is not None and s.status in OPEN_STATUSES:
                        # Drive to 'rejected' through legal path.
                        self.store.inner.save(transition(s, "rejected"))
                    return
                # Persist broker_order_id alongside state.
                state = self.store.load(intent.request_id)
                if state is not None:
                    if state.status == "submitting":
                        state = transition(state, "submitted")
                    state = replace(
                        state,
                        broker_order_id=ack.broker_order_id,
                        submitted_to_broker=True,
                    )
                    self.store.inner.save(state)
                self._broker_order_ids.append(ack.broker_order_id)
                self.events.emit(
                    "broker_submitted",
                    request_id=intent.request_id,
                    broker_order_id=ack.broker_order_id,
                    accepted_qty=ack.accepted_qty,
                )
            except Exception as exc:
                self.events.emit(
                    "broker_place_order_error",
                    request_id=intent.request_id, error=str(exc),
                )

        return handler

    def _build_gate_context(self) -> GateContext:
        runtime = self.runtime
        assert runtime is not None
        ps = PortfolioState(
            cash=float(runtime.cash_value),
            equity=float(runtime.net_asset_value()),
            current_positions=dict(runtime.positions),
            last_close=dict(runtime.last_close),
            market_existing_pct=self.config.market_existing_pct_initial,
            daily_new_pct=self.config.daily_new_pct_initial,
            current_drawdown_pct=self.config.current_drawdown_pct_initial,
        )
        return GateContext(
            portfolio=ps,
            rebalance_date=self.config.rebalance_date,
        )

    # -- order callback (broker → runner) ----------------------------------
    def _on_order_update(self, update: BrokerOrderUpdate) -> None:
        # Look up the corresponding state by broker_order_id when the
        # request_id is missing in the broker payload (futu's "remark" can be
        # truncated). Fall back to remark if available.
        state = self.store.find_by_broker_order_id(update.broker_order_id)
        if state is None and update.request_id:
            state = self.store.load(update.request_id)
        if state is None:
            self.events.emit(
                "order_update_orphan",
                broker_order_id=update.broker_order_id,
                request_id=update.request_id,
                status=update.status,
            )
            return
        # Persist new fill metrics.
        new_state = replace(
            state,
            filled_qty=int(update.filled_qty),
            avg_fill_price=float(update.avg_fill_price),
            broker_order_id=update.broker_order_id,
        )
        # Only attempt status transition if legal.
        try:
            new_state = transition(new_state, update.status)
        except ValueError:
            pass  # ignore illegal transitions; the state record retains last legal status.
        self.store.inner.save(new_state)
        if update.status == "filled":
            self._fill_count += 1
        elif update.status == "cancelled":
            self._cancel_count += 1
        self.events.emit(
            "order_update",
            broker_order_id=update.broker_order_id,
            request_id=state.request_id,
            status=update.status,
            filled_qty=update.filled_qty,
        )

    # -- waiting -----------------------------------------------------------
    def _wait_until_rebalance_time(self) -> None:
        deadline = self.config.rebalance_time_epoch
        if deadline is None:
            return
        last_heartbeat = self._clock()
        while not self._stopping and self._clock() < deadline:
            now = self._clock()
            if now - last_heartbeat >= self.HEARTBEAT_SECONDS:
                self.events.emit("rebalance_wait_heartbeat", remaining=deadline - now)
                last_heartbeat = now
            self._sleep(min(60.0, max(deadline - now, 1.0)))

    def _wait_for_fills_and_cancels(self) -> None:
        wait_deadline = self._clock() + self.config.post_rebalance_wait_seconds
        while not self._stopping and self._clock() < wait_deadline:
            if not self.store.list_open_request_ids():
                return
            self._sleep(min(5.0, max(wait_deadline - self._clock(), 0.1)))

    # -- cleanup -----------------------------------------------------------
    def _cancel_all_open(self) -> None:
        # Snapshot; broker.cancel_order may mutate via callbacks.
        for state in self.store.list():
            if state.status not in OPEN_STATUSES:
                continue
            broker_id = state.broker_order_id
            if not broker_id:
                continue
            try:
                ok = self.broker.cancel_order(broker_id)
                self.events.emit(
                    "cancel_request",
                    broker_order_id=broker_id,
                    request_id=state.request_id,
                    success=ok,
                )
                if ok:
                    self._cancel_count += 1
            except Exception as exc:
                self.events.emit(
                    "cancel_error", broker_order_id=broker_id, error=str(exc),
                )

    def _finalize(self, run_started: float) -> DailyReport:
        cfg = self.config
        runtime = self.runtime
        try:
            self.broker.disconnect()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("broker.disconnect raised: %s", exc)
        if runtime is None:
            return self._build_empty_report("runtime_not_initialised")
        nav = runtime.net_asset_value()
        total_return = (nav - cfg.init_cash) / cfg.init_cash if cfg.init_cash > 0 else 0.0
        report = DailyReport(
            pool=list(cfg.symbols),
            rebalance_date=cfg.rebalance_date,
            rebalance_time=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock())
            ),
            execution_env=cfg.execution_env,
            trade_count=self._intents_approved,
            fill_count=self._fill_count,
            cancel_count=self._cancel_count,
            blocked_by_gate=dict(self._blocked_by_gate),
            final_nav=float(nav),
            final_cash=float(runtime.cash_value),
            total_return_pct=float(total_return),
            connection_drops=0,
            reconcile_breaches=0,
            alerts=list(runtime.alerts_emitted),
            intents_emitted=self._intents_emitted,
            intents_approved=self._intents_approved,
        )
        self._write_products(report)
        return report

    def _build_empty_report(self, reason: str) -> DailyReport:
        cfg = self.config
        self.events.emit("runner_short_circuit", reason=reason)
        return DailyReport(
            pool=list(cfg.symbols),
            rebalance_date=cfg.rebalance_date,
            rebalance_time=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock())
            ),
            execution_env=cfg.execution_env,
        )

    def _write_products(self, report: DailyReport) -> None:
        out_dir = Path(self.config.products_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # daily_report.json
        (out_dir / "daily_report.json").write_text(
            json.dumps(report.__dict__, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        # positions_snapshot.csv
        runtime = self.runtime
        if runtime is None:
            return
        snap = out_dir / "positions_snapshot.csv"
        with open(snap, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "qty", "last_close", "market_value"])
            for sym in runtime.symbols:
                qty = int(runtime.positions.get(sym, 0))
                close = float(runtime.last_close.get(sym, 0.0))
                w.writerow([sym, qty, f"{close:.4f}", f"{qty * close:.4f}"])

    # -- signals -----------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        def _handler(signum, frame):  # pragma: no cover - hard to unit test
            self._stopping = True
            self.events.emit("graceful_shutdown_signal", signum=int(signum))

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
        except (ValueError, AttributeError):  # pragma: no cover - non-main-thread
            # Tests that drive the runner from a thread cannot install signal
            # handlers; we simply skip and rely on the explicit ``_stopping``
            # flag.
            pass
