from __future__ import annotations

"""Classic Multi-factor daily-rebalance runner (S3b skeleton).

This entry is intentionally lightweight compared to ``run_intraday_loop.py``:
it boots the same ``MainEngine + FutuGateway + CtaEngine`` stack with the
same four-gate ``ExecutionGuardPipeline`` but consumes ``interval=1d`` bars
and exits after a configurable number of EOD bars (default 1) or a hard
timeout. Suitable for cron-style invocations of the form::

    python3 scripts/classic_multifactor/run_daily_rebalance.py \\
        --config configs/classic_multifactor/daily_example.json \\
        --rebalance-time 16:05

Daily-only schema fields (``rebalance_time`` / ``max_daily_turnover`` /
``target_positions`` / ``daily_new_pct_limit``) are validated up-front by
``config_schema.validate_loop_mode``; intraday-only fields are rejected.

Status: **skeleton**. Real EOD closed-loop validation is part of Task 7
(S5). For now the runner is wired end-to-end (gateway, pipeline, gates,
order-state persistence, events.jsonl) so the daily input surface is
exercisable, but it does not perform multi-symbol portfolio rebalancing.
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
from scripts.classic_multifactor.run_intraday_loop import (
    GATEWAY_NAME,
    STRATEGY_CLASS,
    _load_simple_yaml_limits,
    _map_vt_symbol,
    _parse_hhmm,
)
from scripts.classic_multifactor.strategy import ClassicMultiFactorCtaStrategy

from services.execution_guard.idempotency import OrderIdempotencyGuard
from services.execution_guard.reconciliation import ReconciliationGuard
from services.futu_account import FutuAccountProvider
from services.risk_engine import LiveRiskGuard
from services.trade_state import OrderStateStore


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classic multi-factor daily-rebalance runner. Single-shot per day. "
            "Dry-run by default; requires --live-submit + VNPY_LIVE_CONFIG=YES "
            "+ VNPY_LIVE_SUBMIT=YES + VNPY_LIVE_APPROVED=YES for real orders."
        )
    )
    parser.add_argument("--config", required=True, type=str)
    parser.add_argument("--strategy-name", type=str, default="")

    parser.add_argument("--rebalance-time", type=str, default="", help="HH:MM in --session-tz; empty = read from config")
    parser.add_argument("--session-tz", type=str, default="America/New_York")
    parser.add_argument("--max-bars", type=int, default=1, help="Stop after this many on_bar calls (default 1 EOD bar)")
    parser.add_argument("--max-runtime-seconds", type=int, default=900, help="Hard timeout after rebalance start")

    parser.add_argument("--live-submit", action="store_true")
    parser.add_argument("--state-root", type=str, default="state/runs")
    parser.add_argument("--report-filename", type=str, default="classic_multifactor_daily_report.json")
    parser.add_argument("--connect-sleep-seconds", type=float, default=15.0)
    parser.add_argument("--strategy-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=10.0)

    parser.add_argument("--max-single-position-pct", type=float, default=None)
    parser.add_argument("--max-daily-new-position-pct", type=float, default=None)
    parser.add_argument("--max-market-exposure-pct", type=float, default=None)
    parser.add_argument("--max-signal-age-seconds", type=int, default=None)
    parser.add_argument("--max-drawdown-pct", type=float, default=None)

    parser.add_argument("--futu-host", type=str, default=os.environ.get("FUTU_HOST", "127.0.0.1"))
    parser.add_argument("--futu-port", type=int, default=int(os.environ.get("FUTU_PORT", "11111")))
    parser.add_argument("--futu-market", type=str, default=os.environ.get("FUTU_MARKET", "US"))
    parser.add_argument("--futu-env", type=str, default=os.environ.get("FUTU_ENV", "模拟"))
    parser.add_argument("--futu-password-env", type=str, default="FUTU_TRADE_PASSWORD")
    return parser


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

@dataclass
class DailyRunnerStats:
    started_at: str = ""
    ended_at: str = ""
    exit_reason: str = ""
    approved: int = 0
    blocked: dict[str, int] = field(default_factory=dict)
    live_submit: bool = False
    strategy_name: str = ""
    vt_symbol: str = ""
    config_path: str = ""
    loop_mode: str = "daily"
    rebalance_time: str = ""
    bars_consumed: int = 0


class DailyRebalanceRunner:
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
        # Schema gate: reject any intraday leak.
        validate_loop_mode(self.payload, "daily", config_path=self.config_path)
        self.setting: dict[str, Any] = dict(self.payload.get("setting") or {})
        self.raw_vt_symbol = str(self.payload.get("symbol") or self.payload.get("vt_symbol") or "")
        if not self.raw_vt_symbol:
            raise ValueError("config missing 'symbol' (e.g. 'NVDA.US')")
        self.vt_symbol = _map_vt_symbol(self.raw_vt_symbol)
        self.market = self.raw_vt_symbol.split(".")[-1].upper()

        self.strategy_name = args.strategy_name or f"classic_multifactor_daily_{self.raw_vt_symbol.replace('.', '_')}"
        self.strategy_id = self.strategy_name

        rebalance_text = args.rebalance_time or str(self.payload.get("rebalance_time", ""))
        rebalance_local = _parse_hhmm(rebalance_text)
        if rebalance_local is None:
            raise ValueError("rebalance_time required either via --rebalance-time or config 'rebalance_time'")
        self.rebalance_time_local: dtime = rebalance_local

        self.daily_new_pct_limit = float(self.payload.get("daily_new_pct_limit", 0.0) or 0.0)
        self.max_daily_turnover = float(self.payload.get("max_daily_turnover", 0.0) or 0.0)

        self.live_submit = self._resolve_live_submit(args.live_submit)

        self.stats = DailyRunnerStats(
            strategy_name=self.strategy_name,
            vt_symbol=self.vt_symbol,
            config_path=str(self.config_path),
            live_submit=self.live_submit,
            rebalance_time=self.rebalance_time_local.strftime("%H:%M"),
        )

        self._stop_requested = False
        self._main_engine: MainEngine | None = None
        self._cta_engine: CtaEngine | None = None
        self._strategy_instance: ClassicMultiFactorCtaStrategy | None = None
        self._pipeline: ExecutionGuardPipeline | None = None
        self._account_provider: FutuAccountProvider | None = None
        self._bars_consumed = 0

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
    # Pipeline wiring (mirrors intraday but daily semantics):
    # * MinuteTradeGuard is constructed with all-zero limits → it becomes a
    #   no-op (the guard short-circuits when ``max_intraday_trades == 0``);
    # * LiveRiskGuard's ``daily_new_pct`` budget can be overridden via the
    #   daily-only ``daily_new_pct_limit`` field.
    # ------------------------------------------------------------------
    def _build_pipeline(self) -> ExecutionGuardPipeline:
        order_store = OrderStateStore(self.orders_root)
        idempotency = OrderIdempotencyGuard(order_store)
        reconciliation_path = self.state_root / "futu_live_position_reconcile.json"
        cold_start_sentinel = self.state_root / ".reconciliation_sentinel"
        reconciliation = ReconciliationGuard(
            reconciliation_path,
            max_age_minutes=24 * 60,  # daily cadence: 1 day staleness OK
            fail_closed=self.live_submit,
            cold_start_sentinel=cold_start_sentinel,
        )

        # Daily mode disables all intraday guard fields by construction.
        minute_guard = MinuteTradeGuard(MinuteTradeGuardConfig(
            max_intraday_trades=0,
            entry_cooldown_minutes=0,
            min_hold_minutes=0,
            no_new_entry_after="",
        ))

        live_limits = _load_simple_yaml_limits(
            self.repo_root / "configs" / "risk" / "live_risk_limits.yaml"
        )
        # Daily-only overrides
        if self.daily_new_pct_limit > 0:
            live_limits["max_daily_new_position_pct"] = self.daily_new_pct_limit
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
            equity = capital
            cash = capital
            symbol_value = 0.0
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
                current_drawdown_pct=0.0,
                signal_age_seconds=0,
                account_status=account_status,
            )

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
            loop_mode="daily",
            live_submit=self.live_submit,
            exchange_tz=self.args.session_tz,
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
            pass

    def _wait_for_rebalance_time(self) -> None:
        while not self._stop_requested:
            now_local = datetime.now(self.tz).time()
            if now_local >= self.rebalance_time_local:
                return
            time.sleep(5.0)

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

        self._account_provider = FutuAccountProvider(
            main_engine=self._main_engine,
            events_log_path=self.events_log_path,
        )

        self._connect_gateway()

        assert self._cta_engine is not None
        self._cta_engine.init_engine()
        self._cta_engine.classes[STRATEGY_CLASS] = ClassicMultiFactorCtaStrategy

        if self.strategy_name not in self._cta_engine.strategies:
            self._cta_engine.add_strategy(
                STRATEGY_CLASS,
                self.strategy_name,
                self.vt_symbol,
                self.setting,
            )

        self._strategy_instance = self._cta_engine.strategies[self.strategy_name]
        self._pipeline = self._build_pipeline()
        self._strategy_instance.execution_hook = self._pipeline

        # Wrap on_bar to count consumed bars and trigger graceful stop.
        original_on_bar = self._strategy_instance.on_bar
        runner = self

        def _wrapped_on_bar(bar):
            try:
                original_on_bar(bar)
            finally:
                runner._bars_consumed += 1
                if runner._bars_consumed >= runner.args.max_bars:
                    runner._stop_requested = True

        self._strategy_instance.on_bar = _wrapped_on_bar  # type: ignore[assignment]

        future = self._cta_engine.init_strategy(self.strategy_name)
        future.result(timeout=self.args.strategy_timeout_seconds)
        logger.info(f"strategy initialised: {self.strategy_name}")
        self._cta_engine.start_strategy(self.strategy_name)
        logger.info(f"strategy started (daily mode, live_submit={self.live_submit})")

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
        if self._main_engine is not None:
            try:
                self._main_engine.close()
            except Exception as exc:
                logger.warning(f"main_engine.close failed: {exc}")

    def _write_report(self) -> Path:
        if self._pipeline is not None:
            self.stats.approved = self._pipeline.approved_count
            self.stats.blocked = dict(self._pipeline.blocked_by_gate)
        self.stats.bars_consumed = self._bars_consumed
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

        self._wait_for_rebalance_time()
        if self._stop_requested:
            self.stats.exit_reason = "stopped_before_rebalance"
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

        deadline = time.time() + max(int(self.args.max_runtime_seconds), 1)
        try:
            while not self._stop_requested:
                if time.time() >= deadline:
                    self._stop_engine("max_runtime_exceeded")
                    break
                time.sleep(self.args.heartbeat_seconds)
            else:
                self._stop_engine("bars_consumed_or_signal")
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
        runner = DailyRebalanceRunner(args)
    except LoopModeValidationError as exc:
        print(f"loop_mode validation failed: {exc}", file=sys.stderr)
        return 3
    except (FileNotFoundError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 3
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
