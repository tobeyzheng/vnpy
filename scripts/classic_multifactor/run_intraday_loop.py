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

def _derive_session_end(setting: dict[str, Any]) -> dtime:
    cutoff = parse_hhmm(str(setting.get("no_new_entry_after", "")))
    if cutoff is None:
        return dtime(16, 30)
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
            parse_hhmm(args.session_end) or _derive_session_end(self.setting)
        )
        self.session_start_local: dtime | None = parse_hhmm(args.session_start)

        self.stats = RunnerStats(
            strategy_name=self.strategy_name,
            vt_symbol=self.vt_symbol,
            config_path=str(self.config_path),
            live_submit=self.live_submit,
            session_end=self.session_end_local.strftime("%H:%M"),
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
