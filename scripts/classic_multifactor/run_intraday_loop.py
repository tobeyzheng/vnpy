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
* Every gate decision is appended to an execution-environment-specific
  ``state/runs/<execution_env>/events.jsonl``; approved orders are persisted
  under ``state/runs/<execution_env>/orders/`` for cross-restart idempotency.
* Warmup bars loaded during ``on_init()`` are used for indicator/model preheat
  only and do not write formal order-state records.
* Handles ``SIGINT``/``SIGTERM`` cleanly; also exits automatically at
  ``--session-end`` when ``--exit-after-session`` is set.

The module deliberately keeps *zero* knowledge of candidate generation / EOD
scoring / daily rebalance — those paths live in ``run_daily_rebalance.py``.

Refactor (C1 / R3): the ``MainEngine + FutuGateway + CtaEngine`` scaffold is
shared with ``run_daily_rebalance.py`` via ``_base_runner.BaseRunner``; this
file now only contains intraday-specific behaviour (session window,
heartbeat loop, intraday guard wiring).
"""

import argparse
import sys
import time
import os
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.classic_multifactor._base_runner import (
    BaseRunner,
    BaseStats,
    GATEWAY_NAME,
    STRATEGY_CLASS,
    add_common_arguments,
    load_simple_yaml_limits,
    map_vt_symbol,
    parse_hhmm,
    runner_main,
)
from scripts.classic_multifactor.minute_guard import (
    MinuteTradeGuard,
    MinuteTradeGuardConfig,
)
from vnpy.trader.logger import logger
from vnpy.trader.object import BarData
from services.strategy.market_rules import market_session_end, market_timezone


# Re-export legacy symbols so existing call-sites that imported them from
# this module continue to work after the refactor.
_map_vt_symbol = map_vt_symbol
_parse_hhmm = parse_hhmm
_load_simple_yaml_limits = load_simple_yaml_limits


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
    add_common_arguments(parser)

    # Intraday-specific overrides / additions.
    parser.set_defaults(strategy_timeout_seconds=180.0)
    parser.add_argument("--session-start", type=str, default="",
                        help="HH:MM in --session-tz; empty = connect immediately")
    parser.add_argument("--session-end", type=str, default="",
                        help="HH:MM in --session-tz; empty = derive from no_new_entry_after+30min, else 16:30")
    parser.add_argument("--exit-after-session", action="store_true", default=True)
    parser.add_argument("--no-exit-after-session", dest="exit_after_session", action="store_false")
    parser.add_argument("--report-filename", type=str,
                        default="classic_multifactor_intraday_report.json")
    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_session_end(setting: dict[str, Any], market_name: str) -> dtime:
    cutoff = parse_hhmm(str(setting.get("no_new_entry_after", "")))
    if cutoff is None:
        return parse_hhmm(market_session_end(market_name)) or dtime(16, 0)
    total = cutoff.hour * 60 + cutoff.minute + 30
    total = min(total, 23 * 60 + 59)
    return dtime(total // 60, total % 60)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class RunnerStats(BaseStats):
    loop_mode: str = "intraday"
    session_end: str = ""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class IntradayLoopRunner(BaseRunner):
    loop_mode = "intraday"

    def __init__(self, args: argparse.Namespace):
        super().__init__(args)
        self.session_end_local: dtime = (
            parse_hhmm(args.session_end) or _derive_session_end(self.setting, self.market_name)
        )
        default_start = parse_hhmm(args.session_start)
        if default_start is None and self.session_tz == market_timezone(self.market_name):
            default_start = parse_hhmm("09:30")
        self.session_start_local: dtime | None = default_start

        self.stats = RunnerStats(
            strategy_name=self.strategy_name,
            vt_symbol=self.vt_symbol,
            config_path=str(self.config_path),
            live_submit=self.live_submit,
            session_end=self.session_end_local.strftime("%H:%M"),
        )
        self._intraday_bars_seen: int = 0
        self._last_bar_monotonic_at: float | None = None
        self._last_bar_exchange_time: str = ""
        self._runtime_debug_heartbeat_seconds: float = 60.0
        self._next_runtime_debug_heartbeat_at: float = (
            time.monotonic() + self._runtime_debug_heartbeat_seconds
        )

    # ------------------------------------------------------------------
    # BaseRunner extension points
    # ------------------------------------------------------------------
    def _stats_factory(self) -> BaseStats:
        return self.stats

    def _build_minute_guard(self) -> MinuteTradeGuard:
        return MinuteTradeGuard(MinuteTradeGuardConfig.from_setting(self.setting))

    def _build_pipeline_extras(self) -> dict[str, Any]:
        # Intraday strategy exposes trade history fields needed by MinuteGuard.
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

        return {
            "trade_times_provider": trade_times_provider,
            "last_trade_provider": last_trade_provider,
            "entry_at_provider": entry_at_provider,
        }

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

        # 增加连接状态检查
        gateway = self._main_engine.get_gateway(GATEWAY_NAME)
        if gateway:
            # 使用正确的属性名检查连接状态
            logger.info(f"FutuGateway连接状态检查: {gateway}")
            logger.info(f"合约订阅状态检查: {self.vt_symbol}")
        else:
            logger.error("FutuGateway连接失败，未找到gateway实例")

    def _post_strategy_attach(self) -> None:
        runner = self

        for instance in self._strategy_instances:
            original_on_bar = instance.on_bar

            def _make_wrapped(strategy_instance, orig):
                def _wrapped_on_bar(bar: BarData):
                    pipeline = runner._pipeline
                    approved_before = pipeline.approved_count if pipeline is not None else 0
                    blocked_before = dict(pipeline.blocked_by_gate) if pipeline is not None else {}
                    active_before = len(getattr(strategy_instance, "active_orderids", set()) or [])
                    error_text = ""

                    # 增加详细的bar接收日志
                    logger.info(
                        f"intraday debug checkpoint: "
                        f"RECEIVED_BAR vt_symbol={bar.vt_symbol} "
                        f"datetime={bar.datetime.isoformat() if bar.datetime else 'none'} "
                        f"open={bar.open_price:.4f} close={bar.close_price:.4f} "
                        f"volume={bar.volume}"
                    )

                    runner._log_intraday_bar_checkpoint(
                        "on_bar_enter",
                        strategy_instance,
                        bar,
                    )

                    try:
                        return orig(bar)
                    except Exception as exc:
                        error_text = str(exc)
                        raise
                    finally:
                        runner._log_intraday_bar_checkpoint(
                            "on_bar_exit",
                            strategy_instance,
                            bar,
                            error_text=error_text,
                        )
                        runner._log_intraday_bar_result(
                            strategy_instance,
                            bar,
                            approved_before=approved_before,
                            blocked_before=blocked_before,
                            active_before=active_before,
                            error_text=error_text,
                        )

                return _wrapped_on_bar

            instance.on_bar = _make_wrapped(instance, original_on_bar)  # type: ignore[assignment]

    def _intraday_debug_state_snapshot(self, strategy_instance) -> dict[str, Any]:
        return {
            "strategy": getattr(strategy_instance, "strategy_name", ""),
            "last_signal": str(getattr(strategy_instance, "last_signal", "")),
            "pos": int(getattr(strategy_instance, "pos", 0) or 0),
            "raw_score": float(getattr(strategy_instance, "raw_score", 0.0) or 0.0),
            "active_orders": len(getattr(strategy_instance, "active_orderids", set()) or []),
            "bars_cached": len(getattr(strategy_instance, "bars", []) or []),
            "is_warmup": bool(getattr(strategy_instance, "_is_warmup", False)),
        }

    def _log_intraday_bar_checkpoint(
        self,
        phase: str,
        strategy_instance,
        bar: BarData,
        *,
        error_text: str = "",
    ) -> None:
        snapshot = self._intraday_debug_state_snapshot(strategy_instance)
        if snapshot["is_warmup"]:
            return
        if phase == "on_bar_enter":
            self._intraday_bars_seen += 1
            self._last_bar_monotonic_at = time.monotonic()
            self._last_bar_exchange_time = (
                bar.datetime.isoformat() if getattr(bar, "datetime", None) else ""
            )
        decision_bar = self._is_intraday_decision_bar(strategy_instance, bar)
        error_suffix = f" error={error_text}" if error_text else ""
        logger.info(
            "intraday debug checkpoint: "
            f"phase={phase} "
            f"strategy={snapshot['strategy']} "
            f"vt_symbol={bar.vt_symbol} "
            f"bar_time={self._last_bar_exchange_time or (bar.datetime.isoformat() if getattr(bar, 'datetime', None) else '')} "
            f"close={float(bar.close_price):.4f} "
            f"decision_bar={decision_bar} "
            f"bars_seen={self._intraday_bars_seen} "
            f"bars_cached={snapshot['bars_cached']} "
            f"is_warmup={snapshot['is_warmup']} "
            f"last_signal={snapshot['last_signal']} "
            f"pos={snapshot['pos']} "
            f"active_orders={snapshot['active_orders']} "
            f"raw_score={snapshot['raw_score']:.4f}"
            f"{error_suffix}"
        )

    def _intraday_runtime_debug_snapshot(self, *, now_monotonic: float | None = None) -> dict[str, Any]:
        snapshot = self._intraday_debug_state_snapshot(self._strategy_instance)
        pipeline = self._pipeline
        blocked_total = sum((pipeline.blocked_by_gate or {}).values()) if pipeline is not None else 0
        if now_monotonic is None:
            now_monotonic = time.monotonic()
        seconds_since_last_bar = None
        if self._last_bar_monotonic_at is not None:
            seconds_since_last_bar = max(now_monotonic - self._last_bar_monotonic_at, 0.0)

        main_engine = getattr(self, "_main_engine", None)
        gateway = main_engine.get_gateway(GATEWAY_NAME) if main_engine is not None else None
        # ``BaseGateway`` does not expose a ``connected`` flag; reading it
        # always returned False even after a successful FutuGateway connect.
        # We therefore derive two more meaningful flags:
        # * ``quote_ctx_attached`` -- whether the FutuGateway holds a live
        #   ``OpenQuoteContext`` (i.e. ``connect_quote()`` finished without
        #   tearing the context down).
        # * ``gateway_data_flowing`` -- whether at least one bar has been
        #   delivered to the strategy in this session, which is the only
        #   end-to-end signal that the data pipeline is actually alive.
        quote_ctx_attached = bool(getattr(gateway, "quote_ctx", None) is not None) if gateway is not None else False
        gateway_data_flowing = self._intraday_bars_seen > 0

        return {
            **snapshot,
            "bars_seen": self._intraday_bars_seen,
            "last_bar_time": self._last_bar_exchange_time or "",
            "seconds_since_last_bar": seconds_since_last_bar,
            "approved_total": pipeline.approved_count if pipeline is not None else 0,
            "blocked_total": blocked_total,
            "quote_ctx_attached": quote_ctx_attached,
            "gateway_data_flowing": gateway_data_flowing,
        }

    def _maybe_log_intraday_runtime_heartbeat(self) -> None:
        now_monotonic = time.monotonic()
        if now_monotonic < self._next_runtime_debug_heartbeat_at:
            return
        snapshot = self._intraday_runtime_debug_snapshot(now_monotonic=now_monotonic)
        seconds_since_last_bar = snapshot["seconds_since_last_bar"]
        seconds_text = "none" if seconds_since_last_bar is None else f"{seconds_since_last_bar:.1f}"

        # Replaced the old ``gateway_connected`` field which was always False
        # (BaseGateway has no such attribute) with two real signals.
        quote_ctx_attached = snapshot.get("quote_ctx_attached", False)
        gateway_data_flowing = snapshot.get("gateway_data_flowing", False)

        logger.info(
            "intraday runtime heartbeat: "
            f"strategy={snapshot['strategy']} "
            f"bars_seen={snapshot['bars_seen']} "
            f"last_bar_time={snapshot['last_bar_time'] or 'none'} "
            f"seconds_since_last_bar={seconds_text} "
            f"quote_ctx_attached={quote_ctx_attached} "
            f"gateway_data_flowing={gateway_data_flowing} "
            f"last_signal={snapshot['last_signal']} "
            f"pos={snapshot['pos']} "
            f"active_orders={snapshot['active_orders']} "
            f"bars_cached={snapshot['bars_cached']} "
            f"approved_total={snapshot['approved_total']} "
            f"blocked_total={snapshot['blocked_total']}"
        )
        self._next_runtime_debug_heartbeat_at = (
            now_monotonic + self._runtime_debug_heartbeat_seconds
        )

    def _log_intraday_bar_result(
        self,
        strategy_instance,
        bar: BarData,
        *,
        approved_before: int,
        blocked_before: dict[str, int],
        active_before: int,
        error_text: str = "",
    ) -> None:
        pipeline = self._pipeline
        approved_after = pipeline.approved_count if pipeline is not None else approved_before
        blocked_after = dict(pipeline.blocked_by_gate) if pipeline is not None else blocked_before
        approved_delta = max(approved_after - approved_before, 0)
        blocked_delta = {
            gate: blocked_after.get(gate, 0) - blocked_before.get(gate, 0)
            for gate in set(blocked_before) | set(blocked_after)
            if blocked_after.get(gate, 0) - blocked_before.get(gate, 0) > 0
        }
        blocked_total_delta = sum(blocked_delta.values())
        active_after = len(getattr(strategy_instance, "active_orderids", set()) or [])
        if not self._should_log_intraday_bar_result(
            strategy_instance,
            bar=bar,
            approved_delta=approved_delta,
            blocked_total_delta=blocked_total_delta,
            active_before=active_before,
            active_after=active_after,
            error_text=error_text,
        ):
            return
        result = self._classify_intraday_bar_result(
            approved_delta=approved_delta,
            blocked_total_delta=blocked_total_delta,
            active_before=active_before,
            active_after=active_after,
            error_text=error_text,
        )
        bar_time = bar.datetime.isoformat() if getattr(bar, "datetime", None) else ""
        last_signal = str(getattr(strategy_instance, "last_signal", ""))
        pos = int(getattr(strategy_instance, "pos", 0) or 0)
        raw_score = float(getattr(strategy_instance, "raw_score", 0.0) or 0.0)
        blocked_summary = ", ".join(
            f"{gate}:{count}" for gate, count in sorted(blocked_delta.items())
        ) or "none"
        error_suffix = f" error={error_text}" if error_text else ""

        logger.info(
            "intraday bar result: "
            f"strategy={strategy_instance.strategy_name} "
            f"vt_symbol={bar.vt_symbol} "
            f"bar_time={bar_time} "
            f"close={float(bar.close_price):.4f} "
            f"result={result} "
            f"last_signal={last_signal} "
            f"approved_delta={approved_delta} "
            f"blocked_delta={blocked_total_delta} "
            f"blocked_by_gate={blocked_summary} "
            f"pos={pos} "
            f"active_orders={active_after} "
            f"raw_score={raw_score:.4f}"
            f"{error_suffix}"
        )

    def _should_log_intraday_bar_result(
        self,
        strategy_instance,
        *,
        bar: BarData,
        approved_delta: int,
        blocked_total_delta: int,
        active_before: int,
        active_after: int,
        error_text: str,
    ) -> bool:
        last_signal = str(getattr(strategy_instance, "last_signal", ""))
        if last_signal in {"warmup", "warming_up"}:
            return False
        if error_text:
            return True
        if approved_delta > 0 or blocked_total_delta > 0:
            return True
        if active_after != active_before:
            return True
        return self._is_intraday_decision_bar(strategy_instance, bar)

    def _is_intraday_decision_bar(self, strategy_instance, bar: BarData) -> bool:
        interval = self._signal_interval_minutes(strategy_instance)
        if interval <= 1:
            return True
        bar_dt = getattr(bar, "datetime", None)
        if bar_dt is None:
            return False
        return int(bar_dt.minute) % interval == 0

    def _signal_interval_minutes(self, strategy_instance) -> int:
        try:
            model = getattr(strategy_instance, "model", None)
            config = getattr(model, "config", None)
            value = int(getattr(config, "signal_interval_minutes", 1) or 1)
        except Exception:
            value = 1
        return max(value, 1)

    def _classify_intraday_bar_result(
        self,
        *,
        approved_delta: int,
        blocked_total_delta: int,
        active_before: int,
        active_after: int,
        error_text: str,
    ) -> str:
        if error_text:
            return "exception"
        if approved_delta > 0:
            return "approved_or_submitted" if self.live_submit else "approved_dry_run"
        if blocked_total_delta > 0:
            return "blocked"
        if active_after > active_before:
            return "order_pending"
        return "no_action"

    def _wait_for_session_start(self) -> None:
        if self.session_start_local is None:
            return
        while not self._stop_requested:
            now_local = datetime.now(self.tz).time()
            if now_local >= self.session_start_local:
                return
            time.sleep(5.0)

    def _should_exit_main_loop(self) -> tuple[bool, str]:
        if not self.args.exit_after_session:
            return False, ""
        self._maybe_log_intraday_runtime_heartbeat()
        now_local = datetime.now(self.tz).time()
        if now_local >= self.session_end_local:
            return True, "session_end"
        return False, ""


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    return runner_main(build_parser, IntradayLoopRunner)


if __name__ == "__main__":
    sys.exit(main())
