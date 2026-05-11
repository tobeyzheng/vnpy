from __future__ import annotations

"""Shared scaffolding for classic_multifactor live runners (C1 / R3).

Both ``run_intraday_loop.py`` and ``run_daily_rebalance.py`` boot exactly the
same ``MainEngine + FutuGateway + CtaEngine`` stack with the same four-gate
``ExecutionGuardPipeline``. Before this refactor the two files contained
~60% byte-level duplicate code (CLI helpers, gateway wiring, engine
lifecycle, report writing). This module collects that scaffold into a
``BaseRunner`` that exposes well-defined extension points for the two
loop modes:

* ``loop_mode``           - "intraday" or "daily"; routed to schema gate.
* ``_build_minute_guard`` - intraday plugs in real cooldowns, daily passes a
  zero-config no-op.
* ``_extra_live_limits``  - daily injects ``daily_new_pct_limit`` overrides.
* ``_wait_for_session_start`` - intraday waits until session_start, daily
  waits until rebalance_time.
* ``_should_exit_main_loop`` - intraday checks session_end, daily checks
  bars consumed and runtime deadline.

The module deliberately does not import any business modules from outside
``services/`` and ``scripts/classic_multifactor/`` so it can be unit-tested
without OpenD.
"""

import argparse
import json
import os
import signal
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.logger import INFO, logger
from vnpy.trader.setting import SETTINGS
from vnpy_ctastrategy import CtaEngine, CtaStrategyApp
from vnpy_futu import FutuGateway

from scripts.classic_multifactor.config_schema import (
    LoopModeValidationError,
    validate_loop_mode,
)
from scripts.classic_multifactor.execution_pipeline import (
    ExecutionGuardPipeline,
    PipelineContext,
)
from scripts.classic_multifactor.minute_guard import MinuteTradeGuard
from scripts.classic_multifactor.strategy import ClassicMultiFactorCtaStrategy

from services.execution_guard.idempotency import OrderIdempotencyGuard
from services.execution_guard.reconciliation import ReconciliationGuard
from services.futu_account import FutuAccountProvider
from services.risk_engine import LiveRiskGuard
from services.trade_state import OmsEventRecorder, OrderStateStore
from services.common.config_loader import load_yaml_limits_block

GATEWAY_NAME = "FUTU"
STRATEGY_CLASS = "ClassicMultiFactorCtaStrategy"


def resolve_execution_env(*, live_submit: bool, futu_env: str) -> str:
    if live_submit:
        return "futu_real"
    env_text = str(futu_env or "").strip().lower()
    if env_text in {"模拟", "simulate", "simulation", "sim"}:
        return "futu_sim"
    return "dry_run"


# ---------------------------------------------------------------------------
# Pure helpers (kept here so both runners share one definition)
# ---------------------------------------------------------------------------

def map_vt_symbol(vt_symbol: str) -> str:
    """Normalize classic config symbols into the gateway-specific vn.py exchange suffix.

    * US equities: ``NVDA.US`` → ``NVDA.SMART`` because ``FutuGateway`` reports
      them under the synthetic ``.SMART`` exchange.
    * Hong Kong equities: ``00700.HK`` → ``00700.SEHK`` because vn.py expects
      the Stock Exchange of Hong Kong exchange suffix during strategy creation.
    """
    if "." not in vt_symbol:
        return vt_symbol
    symbol, exchange = vt_symbol.rsplit(".", 1)
    ex = exchange.upper()
    if ex in ("US", "NASDAQ", "NYSE", "AMEX"):
        return f"{symbol}.SMART"
    if ex == "HK":
        return f"{symbol}.SEHK"
    return vt_symbol


def parse_hhmm(text: str) -> dtime | None:
    text = (text or "").strip()
    if not text:
        return None
    hh, mm = text.split(":", 1)
    return dtime(int(hh), int(mm))


def load_simple_yaml_limits(path: Path) -> dict[str, float]:
    """Read the ``limits:`` block from a risk YAML file.

    Thin wrapper around ``services.common.config_loader.load_yaml_limits_block``
    kept here as a re-export so existing call-sites that imported this
    name continue to work after the C2/R5 consolidation.
    """
    return load_yaml_limits_block(path)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Append the CLI arguments shared by intraday and daily runners.

    Mode-specific arguments (e.g. ``--session-start``, ``--rebalance-time``)
    are still added by the subclass to keep ``--help`` output clean.
    """
    parser.add_argument("--config", required=True, type=str)
    parser.add_argument("--strategy-name", type=str, default="")

    parser.add_argument("--session-tz", type=str, default="America/New_York")

    parser.add_argument("--live-submit", action="store_true",
                        help="Must be combined with env hard-switches to submit real orders")
    parser.add_argument("--state-root", type=str, default="state/runs")
    parser.add_argument("--connect-sleep-seconds", type=float, default=15.0)
    parser.add_argument("--strategy-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=10.0)

    # Risk overrides (optional; fall back to configs/risk/live_risk_limits.yaml)
    parser.add_argument("--max-single-position-pct", type=float, default=None)
    parser.add_argument("--max-daily-new-position-pct", type=float, default=None)
    parser.add_argument("--max-market-exposure-pct", type=float, default=None)
    parser.add_argument("--max-signal-age-seconds", type=int, default=None)
    parser.add_argument("--max-drawdown-pct", type=float, default=None)

    # Gateway creds override (fallback env)
    parser.add_argument("--futu-host", type=str, default=os.environ.get("FUTU_HOST", "127.0.0.1"))
    parser.add_argument("--futu-port", type=int, default=int(os.environ.get("FUTU_PORT", "11111")))
    parser.add_argument("--futu-market", type=str, default=os.environ.get("FUTU_MARKET", "US"))
    parser.add_argument("--futu-env", type=str, default=os.environ.get("FUTU_ENV", "模拟"))
    parser.add_argument("--futu-password-env", type=str, default="FUTU_TRADE_PASSWORD")


# ---------------------------------------------------------------------------
# Stats base
# ---------------------------------------------------------------------------

@dataclass
class BaseStats:
    started_at: str = ""
    ended_at: str = ""
    exit_reason: str = ""
    approved: int = 0
    blocked: dict[str, int] = field(default_factory=dict)
    live_submit: bool = False
    strategy_name: str = ""
    vt_symbol: str = ""
    config_path: str = ""
    loop_mode: str = ""


# ---------------------------------------------------------------------------
# BaseRunner
# ---------------------------------------------------------------------------

class BaseRunner(ABC):
    """Common scaffolding for classic_multifactor MainEngine-based runners."""

    #: Subclasses override with "intraday" or "daily".
    loop_mode: str = ""

    def __init__(self, args: argparse.Namespace, *, default_strategy_prefix: str = "classic_multifactor"):
        self.args = args
        self.repo_root = REPO_ROOT
        self.state_root = (self.repo_root / args.state_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)

        self.execution_env = resolve_execution_env(
            live_submit=self._resolve_live_submit(args.live_submit),
            futu_env=args.futu_env,
        )
        self.execution_state_root = self.state_root / self.execution_env
        self.execution_state_root.mkdir(parents=True, exist_ok=True)
        self.events_log_path = self.execution_state_root / "events.jsonl"
        self.orders_root = self.execution_state_root / "orders"
        self.orders_root.mkdir(parents=True, exist_ok=True)

        self.tz = ZoneInfo(args.session_tz)

        self.config_path = Path(args.config).resolve()
        if not self.config_path.exists():
            raise FileNotFoundError(f"config not found: {self.config_path}")
        self.payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        validate_loop_mode(self.payload, self.loop_mode, config_path=self.config_path)
        self.config_interval = str(self.payload.get("interval") or "1m")
        self.setting: dict[str, Any] = dict(self.payload.get("setting") or {})
        self.setting.setdefault("data_interval", self.config_interval)
        self.raw_vt_symbol = str(self.payload.get("symbol") or self.payload.get("vt_symbol") or "")
        if not self.raw_vt_symbol:
            raise ValueError("config missing 'symbol' (e.g. 'NVDA.US')")
        self.vt_symbol = map_vt_symbol(self.raw_vt_symbol)
        self.market = self.raw_vt_symbol.split(".")[-1].upper()

        symbol_token = self.raw_vt_symbol.replace(".", "_")
        if self.loop_mode == "daily":
            default_name = f"{default_strategy_prefix}_daily_{symbol_token}"
        else:
            default_name = f"{default_strategy_prefix}_{symbol_token}"
        self.strategy_name = args.strategy_name or default_name
        self.strategy_id = self.strategy_name

        self.live_submit = self._resolve_live_submit(args.live_submit)
        self.execution_env = resolve_execution_env(
            live_submit=self.live_submit,
            futu_env=args.futu_env,
        )
        self.execution_state_root = self.state_root / self.execution_env
        self.execution_state_root.mkdir(parents=True, exist_ok=True)
        self.events_log_path = self.execution_state_root / "events.jsonl"
        self.orders_root = self.execution_state_root / "orders"
        self.orders_root.mkdir(parents=True, exist_ok=True)

        self._stop_requested = False
        self._main_engine: MainEngine | None = None
        self._cta_engine: CtaEngine | None = None
        self._strategy_instance: ClassicMultiFactorCtaStrategy | None = None
        self._strategy_instances: list[ClassicMultiFactorCtaStrategy] = []
        self._pipeline: ExecutionGuardPipeline | None = None
        self._account_provider: FutuAccountProvider | None = None
        self._oms_recorder: OmsEventRecorder | None = None

    # ------------------------------------------------------------------
    # Hard-switch gating for live submission
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_live_submit(cli_flag: bool) -> bool:
        if not cli_flag:
            return False
        for env_name in ("VNPY_LIVE_CONFIG", "VNPY_LIVE_SUBMIT", "VNPY_LIVE_APPROVED"):
            if os.environ.get(env_name) != "YES":
                logger.warning(
                    f"--live-submit supplied but {env_name}!=YES — falling back to dry-run"
                )
                return False
        return True

    # ------------------------------------------------------------------
    # Subclass extension points
    # ------------------------------------------------------------------
    @abstractmethod
    def _build_minute_guard(self) -> MinuteTradeGuard:
        """Daily disables it (zero-config); intraday wires real limits."""

    def _extra_live_limits_overrides(self) -> dict[str, float]:
        """Daily injects ``daily_new_pct_limit``; intraday returns {}."""
        return {}

    def _build_pipeline_extras(self) -> dict[str, Any]:
        """Intraday supplies trade_times/last_trade/entry_at providers."""
        return {}

    @abstractmethod
    def _wait_for_session_start(self) -> None:
        """Block until trading should begin (or stop requested)."""

    @abstractmethod
    def _should_exit_main_loop(self) -> tuple[bool, str]:
        """Return (should_exit, reason). Called between heartbeats."""

    @abstractmethod
    def _stats_factory(self) -> BaseStats:
        ...

    def _post_strategy_attach(self) -> None:
        """Hook for subclass to wrap on_bar/on_tick after strategy is created."""

    def _on_pre_start_failure(self) -> str | None:
        """Return optional 'stopped_before_*' reason if startup was aborted."""
        return None

    # ------------------------------------------------------------------
    # Pipeline wiring
    # ------------------------------------------------------------------
    def _build_pipeline(self) -> ExecutionGuardPipeline:
        order_store = OrderStateStore(self.orders_root)
        idempotency = OrderIdempotencyGuard(order_store)

        reconciliation_path = self.state_root / "futu_live_position_reconcile.json"
        cold_start_sentinel = self.state_root / ".reconciliation_sentinel"
        # Daily mode tolerates 1-day staleness; intraday keeps 60min default.
        max_age_minutes = 24 * 60 if self.loop_mode == "daily" else 60
        reconciliation = ReconciliationGuard(
            reconciliation_path,
            max_age_minutes=max_age_minutes,
            fail_closed=self.live_submit,
            cold_start_sentinel=cold_start_sentinel,
        )

        minute_guard = self._build_minute_guard()

        live_limits = load_simple_yaml_limits(
            self.repo_root / "configs" / "risk" / "live_risk_limits.yaml"
        )
        # Daily-mode-only YAML overrides take precedence over the file but
        # are themselves overridden by CLI flags (per-run trumps per-config).
        for key, value in self._extra_live_limits_overrides().items():
            live_limits[key] = value
        for cli_key, setting_key in [
            ("max_single_position_pct", "max_single_position_pct"),
            ("max_daily_new_position_pct", "max_daily_new_position_pct"),
            ("max_market_exposure_pct", "max_market_exposure_pct"),
            ("max_signal_age_seconds", "max_signal_age_seconds"),
            ("max_drawdown_pct", "max_drawdown_pct"),
        ]:
            cli_value = getattr(self.args, cli_key, None)
            if cli_value is not None:
                live_limits[setting_key] = cli_value
        if self.setting.get("max_order_value"):
            live_limits["max_order_value"] = float(self.setting["max_order_value"])
        live_risk = LiveRiskGuard(live_limits)

        capital = float(self.setting.get("capital", 0) or 0)
        target_code = self.raw_vt_symbol.split(".")[0].upper()

        def context_provider() -> PipelineContext:
            equity = capital
            cash = capital
            symbol_value = 0.0
            account_status = "connected"
            if self._account_provider is not None:
                try:
                    summary = self._account_provider.get_summary()
                    equity = float(summary.total_assets or capital)
                    cash = float(summary.cash or capital)
                    for pos in summary.positions:
                        pos_code = str(pos.code or "").split(".")[0].upper()
                        if pos_code == target_code and pos.market_val is not None:
                            symbol_value = float(pos.market_val)
                            break
                except Exception:
                    account_status = "degraded"
            return PipelineContext(
                capital=capital or 1.0,
                equity=equity,
                cash=cash,
                symbol_market_value=symbol_value,
                market_existing_pct=(symbol_value / capital) if capital > 0 else 0.0,
                daily_new_pct=0.0,
                current_drawdown_pct=0.0,
                signal_age_seconds=0,
                account_status=account_status,
            )

        extras = self._build_pipeline_extras()

        return ExecutionGuardPipeline(
            idempotency=idempotency,
            reconciliation=reconciliation,
            minute_guard=minute_guard,
            live_risk=live_risk,
            order_store=order_store,
            events_log_path=self.events_log_path,
            context_provider=context_provider,
            strategy_id=self.strategy_id,
            market=self.market,
            loop_mode=self.loop_mode,
            live_submit=self.live_submit,
            execution_env=self.execution_env,
            exchange_tz=self.args.session_tz,
            oms_recorder=self._oms_recorder,
            **extras,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            logger.info(f"received signal {signum}, requesting graceful stop")
            self._stop_requested = True
        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
        except ValueError:
            # Non-main thread: best-effort only.
            pass

    def _connect_gateway(self) -> None:
        assert self._main_engine is not None
        password = os.environ.get(self.args.futu_password_env, "")
        futu_setting = {
            "密码": password,
            "地址": self.args.futu_host,
            "端口": int(self.args.futu_port),
            "市场": self.args.futu_market,
            "环境": self.args.futu_env,
        }
        logger.info(
            f"connecting FutuGateway at {futu_setting['地址']}:{futu_setting['端口']} "
            f"env={futu_setting['环境']}"
        )
        self._main_engine.connect(futu_setting, GATEWAY_NAME)
        time.sleep(self.args.connect_sleep_seconds)

    def _start_engine(self) -> None:
        SETTINGS["log.active"] = True
        SETTINGS["log.level"] = INFO
        SETTINGS["log.console"] = True
        event_engine = EventEngine()
        self._main_engine = MainEngine(event_engine)
        self._main_engine.add_gateway(FutuGateway)
        self._cta_engine = self._main_engine.add_app(CtaStrategyApp)  # type: ignore[assignment]

        # Read-only account provider attached to OmsEngine (Task 3.1 S2).
        self._account_provider = FutuAccountProvider(
            main_engine=self._main_engine,
            events_log_path=self.events_log_path,
        )

        # OmsEngine event recorder (Task 6 / S4) — drives EVENT_ORDER /
        # EVENT_TRADE pushes into OrderStateStore.
        self._oms_recorder = OmsEventRecorder(
            OrderStateStore(self.orders_root),
            events_log_path=self.events_log_path,
        )
        self._oms_recorder.attach(self._main_engine)

        self._connect_gateway()

        assert self._cta_engine is not None
        self._cta_engine.init_engine()
        # Strategy lives outside ``strategies/`` so we register it manually.
        self._cta_engine.classes[STRATEGY_CLASS] = ClassicMultiFactorCtaStrategy

        # The pipeline is shared across all strategy instances (so that
        # LiveRiskGuard's market-exposure / drawdown limits naturally cover
        # the whole portfolio). Build it before any strategy is registered
        # so warmup load_bar cannot bypass the guards.
        self._pipeline = self._build_pipeline()

        specs = list(self._strategy_specs())
        if not specs:
            raise RuntimeError("no strategy specs supplied; subclass must provide at least one")

        self._strategy_instances = []
        for spec in specs:
            self._register_strategy_for_spec(spec)

        # Backwards compat: legacy single-strategy code paths still expect
        # ``self._strategy_instance``. For multi-symbol daily runs we keep
        # this pointing at the first (config['symbol']) instance so daily-
        # specific wrappers (e.g. on_bar bar-counting) have a stable hook.
        self._strategy_instance = self._strategy_instances[0]

        self._post_strategy_attach()

        for spec_name, _vt, _setting in specs:
            future = self._cta_engine.init_strategy(spec_name)
            future.result(timeout=self.args.strategy_timeout_seconds)
            logger.info(f"strategy initialised: {spec_name}")

        for spec_name, _vt, _setting in specs:
            self._cta_engine.start_strategy(spec_name)
            logger.info(
                f"strategy started: {spec_name} "
                f"(loop_mode={self.loop_mode}, live_submit={self.live_submit})"
            )

    # ------------------------------------------------------------------
    # Strategy registration helpers
    # ------------------------------------------------------------------
    def _strategy_specs(self) -> list[tuple[str, str, dict[str, Any]]]:
        """Return ``[(strategy_name, vt_symbol, setting), ...]`` to register.

        Default: a single-strategy registration mirroring the legacy
        single-symbol behaviour. The daily runner overrides this to fan
        out across ``target_positions``.
        """
        return [(self.strategy_name, self.vt_symbol, dict(self.setting))]

    def _register_strategy_for_spec(
        self, spec: tuple[str, str, dict[str, Any]]
    ) -> None:
        """Register one (name, vt_symbol, setting) and wire the execution hook."""
        assert self._cta_engine is not None
        assert self._pipeline is not None
        spec_name, vt_symbol, setting = spec
        if spec_name not in self._cta_engine.strategies:
            self._cta_engine.add_strategy(
                STRATEGY_CLASS,
                spec_name,
                vt_symbol,
                setting,
            )
            logger.info(f"added strategy {spec_name} on {vt_symbol}")
        instance = self._cta_engine.strategies[spec_name]
        instance.execution_hook = self._pipeline
        self._strategy_instances.append(instance)

    def _stop_engine(self, reason: str) -> None:
        self.stats.exit_reason = reason
        if self._cta_engine is not None and self._strategy_instances:
            for instance in self._strategy_instances:
                try:
                    self._cta_engine.stop_strategy(instance.strategy_name)
                except Exception as exc:
                    logger.warning(
                        f"stop_strategy failed for {instance.strategy_name}: {exc}"
                    )
        if self._account_provider is not None:
            try:
                self._account_provider.detach_main_engine()
            except Exception:
                pass
        if self._oms_recorder is not None:
            try:
                self._oms_recorder.detach()
            except Exception:
                pass
        if self._main_engine is not None:
            try:
                self._main_engine.close()
            except Exception as exc:
                logger.warning(f"main_engine.close failed: {exc}")

    # ------------------------------------------------------------------
    # Reporting (subclasses override to add mode-specific fields)
    # ------------------------------------------------------------------
    def _populate_stats_for_report(self) -> None:
        if self._pipeline is not None:
            self.stats.approved = self._pipeline.approved_count
            self.stats.blocked = dict(self._pipeline.blocked_by_gate)

    def _write_report(self) -> Path:
        from dataclasses import asdict
        self._populate_stats_for_report()
        self.stats.ended_at = datetime.now(timezone.utc).isoformat()
        report_path = self.state_root / self.args.report_filename
        report_path.write_text(
            json.dumps(asdict(self.stats), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return report_path

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def run(self) -> int:
        self._install_signal_handlers()
        self.stats.started_at = datetime.now(timezone.utc).isoformat()

        self._wait_for_session_start()
        if self._stop_requested:
            self.stats.exit_reason = self._on_pre_start_failure() or "stopped_before_start"
            self._write_report()
            return 0

        try:
            self._start_engine()
        except Exception as exc:
            logger.error(f"engine start failed: {exc}")
            self.stats.exit_reason = f"start_failed:{exc}"
            self._stop_engine("start_failed")
            self._write_report()
            return 2

        try:
            while not self._stop_requested:
                should_exit, reason = self._should_exit_main_loop()
                if should_exit:
                    logger.info(f"exit condition met: {reason}")
                    self._stop_engine(reason)
                    break
                time.sleep(self.args.heartbeat_seconds)
            else:
                self._stop_engine("signal")
        except KeyboardInterrupt:
            self._stop_engine("keyboard_interrupt")

        report_path = self._write_report()
        logger.info(f"report written to {report_path}")
        return 0


# ---------------------------------------------------------------------------
# Entrypoint helper
# ---------------------------------------------------------------------------

def runner_main(
    parser_factory: Callable[[], argparse.ArgumentParser],
    runner_factory: Callable[[argparse.Namespace], BaseRunner],
) -> int:
    args = parser_factory().parse_args()
    try:
        runner = runner_factory(args)
    except LoopModeValidationError as exc:
        print(f"loop_mode validation failed: {exc}", file=sys.stderr)
        return 3
    except (FileNotFoundError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 3
    return runner.run()
