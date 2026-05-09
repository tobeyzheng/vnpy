from __future__ import annotations

"""Classic Multi-factor minute-level live runner (S3a).

Standalone entry that keeps a ``MainEngine + FutuGateway + CtaEngine`` alive
for the duration of a US trading session and runs
``ClassicMultiFactorCtaStrategy`` with a four-stage pre-trade guard pipeline
(``Idempotency → Reconciliation → MinuteGuard → LiveRiskGuard``).

Highlights
----------
* Consumes ``configs/classic_multifactor/*.json`` directly (``loop_mode`` must
  be ``intraday``; the schema check rejects any mixed daily-only fields).
* Dry-run by default. Real order submission requires **all** of
  ``--live-submit`` + ``VNPY_LIVE_CONFIG=YES`` + ``VNPY_LIVE_SUBMIT=YES`` +
  ``VNPY_LIVE_APPROVED=YES``.
* Every gate decision is appended to ``state/runs/events.jsonl``; approved
  orders also get a persistent ``OrderStateStore`` entry for cross-restart
  idempotency.
* Handles ``SIGINT``/``SIGTERM`` cleanly; also exits automatically at
  ``--session-end`` when ``--exit-after-session`` is set.

The module deliberately keeps *zero* knowledge of candidate generation / EOD
scoring / daily rebalance — those paths live in ``run_daily_rebalance.py``.
"""

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any
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
from scripts.classic_multifactor.minute_guard import MinuteTradeGuardConfig, MinuteTradeGuard
from scripts.classic_multifactor.strategy import ClassicMultiFactorCtaStrategy

from services.execution_guard.idempotency import OrderIdempotencyGuard
from services.execution_guard.reconciliation import ReconciliationGuard
from services.futu_account import FutuAccountProvider
from services.risk_engine import LiveRiskGuard
from services.trade_state import OmsEventRecorder, OrderStateStore

GATEWAY_NAME = "FUTU"
STRATEGY_CLASS = "ClassicMultiFactorCtaStrategy"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classic multi-factor minute-level live runner. Dry-run by default; "
            "requires --live-submit + VNPY_LIVE_CONFIG=YES + VNPY_LIVE_SUBMIT=YES "
            "+ VNPY_LIVE_APPROVED=YES for real order submission."
        )
    )
    parser.add_argument("--config", required=True, type=str, help="Path to classic_multifactor JSON config")
    parser.add_argument("--strategy-name", type=str, default="", help="Override strategy instance name (defaults to <class>_<symbol>)")

    parser.add_argument("--session-start", type=str, default="", help="HH:MM in --session-tz; empty = connect immediately")
    parser.add_argument("--session-end", type=str, default="", help="HH:MM in --session-tz; empty = derive from no_new_entry_after+30min, else 16:30")
    parser.add_argument("--session-tz", type=str, default="America/New_York")
    parser.add_argument("--exit-after-session", action="store_true", default=True)
    parser.add_argument("--no-exit-after-session", dest="exit_after_session", action="store_false")

    parser.add_argument("--live-submit", action="store_true", help="Must be combined with env hard-switches to submit real orders")
    parser.add_argument("--state-root", type=str, default="state/runs", help="Root directory for run-state outputs")
    parser.add_argument("--report-filename", type=str, default="classic_multifactor_intraday_report.json")
    parser.add_argument("--heartbeat-seconds", type=float, default=10.0)
    parser.add_argument("--connect-sleep-seconds", type=float, default=15.0)
    parser.add_argument("--strategy-timeout-seconds", type=float, default=180.0)

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
    parser.add_argument("--futu-env", type=str, default=os.environ.get("FUTU_ENV", "模拟"), help="Futu env: 模拟 / 真实")
    parser.add_argument("--futu-password-env", type=str, default="FUTU_TRADE_PASSWORD")
    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_vt_symbol(vt_symbol: str) -> str:
    """Convert classic config-style ``NVDA.US`` into Futu OMS-style ``NVDA.SMART``.

    ``FutuGateway`` reports all US equities under the synthetic ``.SMART``
    exchange; strategies subscribing under ``.US`` would receive no ticks.
    """
    if "." not in vt_symbol:
        return vt_symbol
    symbol, exchange = vt_symbol.rsplit(".", 1)
    ex = exchange.upper()
    if ex in ("US", "NASDAQ", "NYSE", "AMEX"):
        return f"{symbol}.SMART"
    return vt_symbol


def _parse_hhmm(text: str) -> dtime | None:
    text = (text or "").strip()
    if not text:
        return None
    hh, mm = text.split(":", 1)
    return dtime(int(hh), int(mm))


def _derive_session_end(setting: dict[str, Any]) -> dtime:
    cutoff = _parse_hhmm(str(setting.get("no_new_entry_after", "")))
    if cutoff is None:
        return dtime(16, 30)
    total = cutoff.hour * 60 + cutoff.minute + 30
    total = min(total, 23 * 60 + 59)
    return dtime(total // 60, total % 60)


def _load_simple_yaml_limits(path: Path) -> dict[str, float]:
    """Tiny hand-rolled YAML reader identical to services/trading_pipeline/live_task.py.

    Avoids pulling in a full PyYAML dependency just for two keys per file.
    """
    limits: dict[str, float] = {}
    if not path.exists():
        return limits
    in_block = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("limits:"):
            in_block = True
            continue
        if not in_block:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and not line.startswith("\t"):
            in_block = False
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        text = value.strip()
        try:
            number = float(text)
            limits[key.strip()] = int(number) if number.is_integer() else number
        except ValueError:
            limits[key.strip()] = text
    return limits


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

@dataclass
class RunnerStats:
    started_at: str = ""
    ended_at: str = ""
    exit_reason: str = ""
    approved: int = 0
    blocked: dict[str, int] = field(default_factory=dict)
    live_submit: bool = False
    strategy_name: str = ""
    vt_symbol: str = ""
    config_path: str = ""
    loop_mode: str = "intraday"
    session_end: str = ""


class IntradayLoopRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.repo_root = REPO_ROOT
        self.state_root = (self.repo_root / args.state_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)

        self.events_log_path = self.state_root / "events.jsonl"
        self.orders_root = self.state_root / "orders"
        self.orders_root.mkdir(parents=True, exist_ok=True)

        self.tz = ZoneInfo(args.session_tz)

        self.config_path = Path(args.config).resolve()
        if not self.config_path.exists():
            raise FileNotFoundError(f"config not found: {self.config_path}")
        self.payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        # Schema gate #1: loop_mode must be intraday and no daily-only fields.
        validate_loop_mode(self.payload, "intraday", config_path=self.config_path)
        self.setting: dict[str, Any] = dict(self.payload.get("setting") or {})
        self.raw_vt_symbol = str(self.payload.get("symbol") or self.payload.get("vt_symbol") or "")
        if not self.raw_vt_symbol:
            raise ValueError("config missing 'symbol' (e.g. 'NVDA.US')")
        self.vt_symbol = _map_vt_symbol(self.raw_vt_symbol)
        self.market = self.raw_vt_symbol.split(".")[-1].upper()

        self.strategy_name = args.strategy_name or f"classic_multifactor_{self.raw_vt_symbol.replace('.', '_')}"
        self.strategy_id = self.strategy_name

        self.session_end_local = _parse_hhmm(args.session_end) or _derive_session_end(self.setting)
        self.session_start_local = _parse_hhmm(args.session_start)

        self.live_submit = self._resolve_live_submit(args.live_submit)

        self.stats = RunnerStats(
            strategy_name=self.strategy_name,
            vt_symbol=self.vt_symbol,
            config_path=str(self.config_path),
            live_submit=self.live_submit,
            session_end=self.session_end_local.strftime("%H:%M"),
        )

        self._stop_requested = False
        self._main_engine: MainEngine | None = None
        self._cta_engine: CtaEngine | None = None
        self._strategy_instance: ClassicMultiFactorCtaStrategy | None = None
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
    # Pipeline wiring
    # ------------------------------------------------------------------
    def _build_pipeline(self) -> ExecutionGuardPipeline:
        order_store = OrderStateStore(self.orders_root)
        idempotency = OrderIdempotencyGuard(order_store)

        reconciliation_path = self.state_root / "futu_live_position_reconcile.json"
        cold_start_sentinel = self.state_root / ".reconciliation_sentinel"
        reconciliation = ReconciliationGuard(
            reconciliation_path,
            max_age_minutes=60,
            fail_closed=self.live_submit,
            cold_start_sentinel=cold_start_sentinel,
        )

        minute_guard = MinuteTradeGuard(MinuteTradeGuardConfig.from_setting(self.setting))

        live_limits = _load_simple_yaml_limits(
            self.repo_root / "configs" / "risk" / "live_risk_limits.yaml"
        )
        # CLI overrides take priority.
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

        def context_provider() -> PipelineContext:
            # Pull up-to-date snapshots from OmsEngine via the shared
            # FutuAccountProvider. The provider owns freshness / fallback
            # semantics (Task 3.x S2).
            equity = capital
            cash = capital
            symbol_value = 0.0
            drawdown = 0.0
            account_status = "connected"
            if self._account_provider is not None:
                try:
                    summary = self._account_provider.get_summary()
                    equity = float(summary.total_assets or capital)
                    cash = float(summary.cash or capital)
                    target_code = self.raw_vt_symbol.split(".")[0].upper()
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
                current_drawdown_pct=drawdown,
                signal_age_seconds=0,
                account_status=account_status,
            )

        def trade_times_provider():
            if self._strategy_instance is None:
                return []
            return list(self._strategy_instance.trade_times)

        def last_trade_provider():
            if self._strategy_instance is None:
                return None
            return self._strategy_instance.last_trade_at

        def entry_at_provider():
            if self._strategy_instance is None:
                return None
            return self._strategy_instance.entry_at

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
            loop_mode="intraday",
            live_submit=self.live_submit,
            trade_times_provider=trade_times_provider,
            last_trade_provider=last_trade_provider,
            entry_at_provider=entry_at_provider,
            exchange_tz=self.args.session_tz,
            oms_recorder=self._oms_recorder,
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

    def _wait_for_session_start(self) -> None:
        if self.session_start_local is None:
            return
        while not self._stop_requested:
            now_local = datetime.now(self.tz).time()
            if now_local >= self.session_start_local:
                return
            time.sleep(5.0)

    def _is_session_over(self) -> bool:
        now_local = datetime.now(self.tz).time()
        return now_local >= self.session_end_local

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
        logger.info(f"connecting FutuGateway at {futu_setting['地址']}:{futu_setting['端口']} env={futu_setting['环境']}")
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

        # Attach the read-only account provider to OmsEngine (Task 3.1 S2).
        self._account_provider = FutuAccountProvider(
            main_engine=self._main_engine,
            events_log_path=self.events_log_path,
        )

        # Attach the OmsEngine event recorder (Task 6 / S4) so EVENT_ORDER
        # / EVENT_TRADE pushes flow into OrderStateStore as the post-submit
        # source of truth.
        self._oms_recorder = OmsEventRecorder(
            OrderStateStore(self.orders_root),
            events_log_path=self.events_log_path,
        )
        self._oms_recorder.attach(self._main_engine)

        self._connect_gateway()

        assert self._cta_engine is not None
        self._cta_engine.init_engine()

        # Inject our strategy class into the engine — we deliberately keep
        # ``strategy.py`` outside ``strategies/`` so ``cta_engine.classes`` is
        # blank for us by default.
        self._cta_engine.classes[STRATEGY_CLASS] = ClassicMultiFactorCtaStrategy

        if self.strategy_name not in self._cta_engine.strategies:
            self._cta_engine.add_strategy(
                STRATEGY_CLASS,
                self.strategy_name,
                self.vt_symbol,
                self.setting,
            )
            logger.info(f"added strategy {self.strategy_name} on {self.vt_symbol}")

        # Wire the execution pipeline into the strategy instance BEFORE
        # init_strategy so that any load_bar warmup cannot accidentally place
        # orders without going through the guards.
        self._strategy_instance = self._cta_engine.strategies[self.strategy_name]
        self._pipeline = self._build_pipeline()
        self._strategy_instance.execution_hook = self._pipeline

        future = self._cta_engine.init_strategy(self.strategy_name)
        future.result(timeout=self.args.strategy_timeout_seconds)
        logger.info(f"strategy initialised: {self.strategy_name}")

        self._cta_engine.start_strategy(self.strategy_name)
        logger.info(f"strategy started: {self.strategy_name} (live_submit={self.live_submit})")

    def _stop_engine(self, reason: str) -> None:
        self.stats.exit_reason = reason
        if self._cta_engine is not None and self._strategy_instance is not None:
            try:
                self._cta_engine.stop_strategy(self.strategy_name)
            except Exception as exc:
                logger.warning(f"stop_strategy failed: {exc}")
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

    def _write_report(self) -> Path:
        if self._pipeline is not None:
            self.stats.approved = self._pipeline.approved_count
            self.stats.blocked = dict(self._pipeline.blocked_by_gate)
        self.stats.ended_at = datetime.now(timezone.utc).isoformat()
        report_path = self.state_root / self.args.report_filename
        report_path.write_text(
            json.dumps(asdict(self.stats), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return report_path

    def run(self) -> int:
        self._install_signal_handlers()
        self.stats.started_at = datetime.now(timezone.utc).isoformat()

        self._wait_for_session_start()
        if self._stop_requested:
            self.stats.exit_reason = "stopped_before_start"
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

        # Main heartbeat loop.
        try:
            while not self._stop_requested:
                if self.args.exit_after_session and self._is_session_over():
                    logger.info(f"session end reached ({self.session_end_local.strftime('%H:%M')}), exiting")
                    self._stop_engine("session_end")
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
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    args = build_parser().parse_args()
    try:
        runner = IntradayLoopRunner(args)
    except LoopModeValidationError as exc:
        print(f"loop_mode validation failed: {exc}", file=sys.stderr)
        return 3
    except (FileNotFoundError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 3
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
