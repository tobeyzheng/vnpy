from __future__ import annotations

"""Classic Multi-factor daily-rebalance runner (S3b skeleton).

This entry is intentionally lightweight compared to ``run_intraday_loop.py``:
it boots the same ``MainEngine + FutuGateway + CtaEngine`` stack with the
same four-gate ``ExecutionGuardPipeline`` but consumes ``interval=1d`` bars
and exits after a configurable number of EOD bars (default 1) or a hard
timeout. Suitable for cron-style invocations of the form::

    python3 scripts/classic_multifactor/run_daily_rebalance.py \
        --config configs/classic_multifactor/daily_example.json \
        --rebalance-time 16:05

Daily-only schema fields (``rebalance_time`` / ``max_daily_turnover`` /
``target_positions`` / ``daily_new_pct_limit``) are validated up-front by
``config_schema.validate_loop_mode``; intraday-only fields are rejected.

Status: **skeleton**. Real EOD closed-loop validation is part of Task 7
(S5). For now the runner is wired end-to-end (gateway, pipeline, gates,
order-state persistence, execution-environment-specific ``events.jsonl`` /
``orders/`` directories) so the daily input surface is exercisable, but it
does not perform multi-symbol portfolio rebalancing. Warmup bars loaded by
strategy ``on_init()`` are only used for preheat and do not write formal
order-state records.

Refactor (C1 / R3): the ``MainEngine + FutuGateway + CtaEngine`` scaffold is
shared with ``run_intraday_loop.py`` via ``_base_runner.BaseRunner``; this
file now only contains daily-specific behaviour (rebalance-time wait, bar
counting, daily-only YAML overrides).
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.classic_multifactor._base_runner import (
    BaseRunner,
    BaseStats,
    add_common_arguments,
    parse_hhmm,
    runner_main,
)
from scripts.classic_multifactor.minute_guard import (
    MinuteTradeGuard,
    MinuteTradeGuardConfig,
)
from vnpy.trader.logger import logger
from services.strategy.market_rules import market_daily_rebalance_time


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
    add_common_arguments(parser)

    parser.add_argument("--rebalance-time", type=str, default="",
                        help="HH:MM in --session-tz; empty = read from config")
    parser.add_argument("--max-bars", type=int, default=1,
                        help="Stop after this many on_bar calls (default 1 EOD bar)")
    parser.add_argument("--max-runtime-seconds", type=int, default=900,
                        help="Hard timeout after rebalance start")
    parser.add_argument("--report-filename", type=str,
                        default="classic_multifactor_daily_report.json")
    return parser


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class DailyRunnerStats(BaseStats):
    loop_mode: str = "daily"
    rebalance_time: str = ""
    bars_consumed: int = 0
    symbol_count: int = 1
    target_positions: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class DailyRebalanceRunner(BaseRunner):
    loop_mode = "daily"

    def __init__(self, args: argparse.Namespace):
        super().__init__(args)
        rebalance_text = (
            args.rebalance_time
            or str(self.payload.get("rebalance_time", ""))
            or market_daily_rebalance_time(self.market_name)
        )
        rebalance_local = parse_hhmm(rebalance_text)
        if rebalance_local is None:
            raise ValueError(
                "rebalance_time required either via --rebalance-time or config 'rebalance_time'"
            )
        self.rebalance_time_local: dtime = rebalance_local

        self.daily_new_pct_limit = float(self.payload.get("daily_new_pct_limit", 0.0) or 0.0)
        self.max_daily_turnover = float(self.payload.get("max_daily_turnover", 0.0) or 0.0)

        # Multi-symbol target_positions parsing (Task 7 / S5).
        # ``target_positions`` is a free-form mapping ``{vt_symbol: weight}``;
        # each entry registers an independent ClassicMultiFactorCtaStrategy
        # instance sharing the same MainEngine/CtaEngine/ExecutionGuardPipeline.
        # Falls back to the legacy single-symbol behaviour when target_positions
        # is missing or empty (one strategy on payload['symbol']).
        self._target_positions: dict[str, float] = self._parse_target_positions()

        self._bars_consumed = 0
        self._deadline: float | None = None
        self._wait_heartbeat_seconds = 10 * 60

        self.stats = DailyRunnerStats(
            strategy_name=self.strategy_name,
            vt_symbol=self.vt_symbol,
            config_path=str(self.config_path),
            live_submit=self.live_submit,
            rebalance_time=self.rebalance_time_local.strftime("%H:%M"),
        )
        self.stats.target_positions = dict(self._target_positions)
        self.stats.symbol_count = len(self._target_positions) or 1

    # ------------------------------------------------------------------
    # target_positions parsing
    # ------------------------------------------------------------------
    def _parse_target_positions(self) -> dict[str, float]:
        raw = self.payload.get("target_positions") or {}
        if not isinstance(raw, dict):
            raise ValueError(
                f"target_positions must be a mapping {{vt_symbol: weight}}; got {type(raw).__name__}"
            )
        out: dict[str, float] = {}
        for key, value in raw.items():
            try:
                weight = float(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"target_positions[{key!r}] must be a number, got {value!r}"
                ) from None
            if weight < 0 or weight > 1.0:
                raise ValueError(
                    f"target_positions[{key!r}]={weight} out of range [0, 1]"
                )
            out[str(key)] = weight
        # Cross-check against max_daily_turnover (sum of new positions per day
        # cannot exceed the global cap; this is a soft warning, the actual
        # enforcement happens in LiveRiskGuard at submission time).
        total = sum(out.values())
        if self.max_daily_turnover > 0 and total > self.max_daily_turnover * 5:
            # 5x slack: target_positions is the *target* exposure, turnover is
            # the *change*, so we only warn on egregious mismatches.
            from vnpy.trader.logger import logger as _logger
            _logger.warning(
                f"target_positions sum={total:.3f} substantially exceeds "
                f"5 * max_daily_turnover ({self.max_daily_turnover:.3f}); "
                "verify the config is not double-booking the budget."
            )
        return out

    # ------------------------------------------------------------------
    # BaseRunner extension points
    # ------------------------------------------------------------------
    def _stats_factory(self) -> BaseStats:
        return self.stats

    def _strategy_specs(self) -> list[tuple[str, str, dict[str, Any]]]:
        # Single-symbol fallback: empty target_positions == legacy behaviour.
        if not self._target_positions:
            return [(self.strategy_name, self.vt_symbol, dict(self.setting))]

        specs: list[tuple[str, str, dict[str, Any]]] = []
        for raw_vt, weight in self._target_positions.items():
            from scripts.classic_multifactor._base_runner import map_vt_symbol
            vt_symbol = map_vt_symbol(raw_vt)
            symbol_token = raw_vt.replace(".", "_")
            spec_name = f"classic_multifactor_daily_{symbol_token}"
            # Per-symbol setting = base setting with target-weight-driven
            # max_position_pct override. Capital is divided across symbols
            # so each strategy sees its own slice.
            per_setting = dict(self.setting)
            per_setting["max_position_pct"] = float(weight)
            # If the user supplied a global capital, give each symbol the
            # same total (LiveRiskGuard enforces portfolio-wide caps via
            # context_provider.equity, so this is a per-strategy book-
            # keeping value, not a hard budget).
            specs.append((spec_name, vt_symbol, per_setting))
        return specs

    def _build_minute_guard(self) -> MinuteTradeGuard:
        # Daily mode disables all intraday guard fields by construction
        # (MinuteTradeGuard short-circuits when max_intraday_trades == 0).
        return MinuteTradeGuard(MinuteTradeGuardConfig(
            max_intraday_trades=0,
            entry_cooldown_minutes=0,
            min_hold_minutes=0,
            no_new_entry_after="",
        ))

    def _extra_live_limits_overrides(self) -> dict[str, float]:
        if self.daily_new_pct_limit > 0:
            return {"max_daily_new_position_pct": self.daily_new_pct_limit}
        return {}

    def _post_strategy_attach(self) -> None:
        # Wrap on_bar across ALL registered strategies so we count every
        # EOD bar consumed (one per symbol) and stop after
        # ``max_bars * symbol_count`` (i.e. after every symbol has
        # processed ``max_bars`` bars).
        runner = self
        target = max(int(runner.args.max_bars), 1) * max(len(runner._strategy_instances), 1)

        for instance in self._strategy_instances:
            original_on_bar = instance.on_bar

            def _make_wrapped(orig):
                def _wrapped_on_bar(bar):
                    try:
                        orig(bar)
                    finally:
                        runner._bars_consumed += 1
                        if runner._bars_consumed >= target:
                            runner._stop_requested = True
                return _wrapped_on_bar

            instance.on_bar = _make_wrapped(original_on_bar)  # type: ignore[assignment]

    def _log_waiting_status(self, *, heartbeat: bool) -> None:
        now_local = datetime.now(self.tz)
        now_minutes = now_local.hour * 60 + now_local.minute
        rebalance_minutes = self.rebalance_time_local.hour * 60 + self.rebalance_time_local.minute
        remaining_minutes = max(rebalance_minutes - now_minutes, 0)
        phase = "daily runner heartbeat" if heartbeat else "daily runner waiting"
        logger.info(
            f"{phase}: strategy={self.strategy_name} "
            f"rebalance_time={self.rebalance_time_local.strftime('%H:%M')} "
            f"now={now_local.strftime('%Y-%m-%d %H:%M:%S %Z')} "
            f"remaining_minutes={remaining_minutes} "
            f"live_submit={self.live_submit}"
        )

    def _wait_for_session_start(self) -> None:
        # Daily mode waits until rebalance_time in session_tz.
        self._log_waiting_status(heartbeat=False)
        next_heartbeat_at = time.monotonic() + float(self._wait_heartbeat_seconds)
        while not self._stop_requested:
            now_local = datetime.now(self.tz).time()
            if now_local >= self.rebalance_time_local:
                logger.info(
                    f"daily runner reached rebalance_time={self.rebalance_time_local.strftime('%H:%M')} "
                    f"strategy={self.strategy_name}; starting engine"
                )
                return
            if time.monotonic() >= next_heartbeat_at:
                self._log_waiting_status(heartbeat=True)
                next_heartbeat_at = time.monotonic() + float(self._wait_heartbeat_seconds)
            time.sleep(5.0)

    def _on_pre_start_failure(self) -> str | None:
        return "stopped_before_rebalance"

    def _should_exit_main_loop(self) -> tuple[bool, str]:
        if self._deadline is None:
            self._deadline = time.time() + max(int(self.args.max_runtime_seconds), 1)
        if time.time() >= self._deadline:
            return True, "max_runtime_exceeded"
        # When max_bars is hit the wrapped on_bar flips _stop_requested,
        # which is then surfaced by BaseRunner.run() as exit reason "signal".
        # Override that to a clearer reason if the bar-count was the trigger.
        target = max(int(self.args.max_bars), 1) * max(len(self._strategy_instances), 1)
        if self._stop_requested and self._bars_consumed >= target:
            return True, "bars_consumed_or_signal"
        return False, ""

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def _populate_stats_for_report(self) -> None:
        super()._populate_stats_for_report()
        self.stats.bars_consumed = self._bars_consumed


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    return runner_main(build_parser, DailyRebalanceRunner)


if __name__ == "__main__":
    sys.exit(main())
