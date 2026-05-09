"""Shared helpers between ``run_loop.py`` (per-symbol) and
``run_portfolio_loop.py`` (portfolio-level).

Both scripts run a long-lived outer loop that:

* respects a Beijing-time session window (US market 22:30 -> 05:00),
* spawns a short-lived child process per iteration,
* reads back the child's ``live_report.json`` to drive an anchor-based
  circuit breaker,
* fingerprints the broker account so a fresh deploy against a different
  account invalidates yesterday's anchor automatically,
* ensures SIGINT / SIGTERM tear the child down cleanly and does *not*
  orphan sub-processes on exit.

The code used to live duplicated in both files; consolidating here keeps the
two schedulers behaviourally identical and removes ~250 lines of drift.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

BEIJING_TZ = timezone(timedelta(hours=8))

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT / "state" / "runs"


# ---------------------------------------------------------------------------
# Time / session helpers
# ---------------------------------------------------------------------------
def parse_hhmm(s: str) -> dtime:
    hh, mm = s.split(":")
    return dtime(int(hh), int(mm))


def now_bj() -> datetime:
    return datetime.now(tz=BEIJING_TZ)


def in_session(now: datetime, start: dtime, end: dtime) -> bool:
    """Whether ``now`` falls inside a possibly cross-midnight window."""
    cur = now.timetz().replace(tzinfo=None)
    cur_t = dtime(cur.hour, cur.minute, cur.second)
    if start <= end:
        return start <= cur_t < end
    return cur_t >= start or cur_t < end


def seconds_until(now: datetime, target: dtime) -> float:
    target_dt = now.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if target_dt <= now:
        target_dt += timedelta(days=1)
    return (target_dt - now).total_seconds()


# ---------------------------------------------------------------------------
# Report / NAV helpers
# ---------------------------------------------------------------------------
def report_path_for(symbol: str) -> Path:
    """Standard live_report.json location written by
    ``LiveTradingPipeline.run_once`` for a given symbol.
    """
    return LOG_DIR / f"classic_multifactor_{symbol.replace('.', '_')}_live_report.json"


def anchor_path_for_symbol(symbol: str) -> Path:
    tag = f"classic_multifactor_{symbol.replace('.', '_')}"
    return LOG_DIR / f"loop_anchor_{tag}.json"


def anchor_path_for_portfolio(portfolio_name: str) -> Path:
    return LOG_DIR / f"loop_anchor_portfolio_{portfolio_name}.json"


def read_current_nav(symbol: str) -> tuple[float | None, float | None, dict[str, Any] | None]:
    """Return ``(total_nav, cash, raw_report)`` from the latest report for
    ``symbol``, or ``(None, None, None)`` if the report is missing / unparsable.
    """
    path = report_path_for(symbol)
    if not path.exists():
        return None, None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, None, None
    selected = data.get("selected") or []
    if not selected:
        return None, None, data
    rc = (selected[0] or {}).get("risk_context") or {}
    try:
        nav = float(rc.get("total_nav")) if rc.get("total_nav") is not None else None
    except Exception:
        nav = None
    try:
        cash = float(rc.get("cash")) if rc.get("cash") is not None else None
    except Exception:
        cash = None
    return nav, cash, data


def extract_env_fingerprint(report: dict[str, Any] | None) -> dict[str, str | None]:
    """Extract (env, account_last4, market) from a live report for anchor
    fingerprinting.
    """
    if not report:
        return {"env": None, "account_last4": None, "market": None}
    market = report.get("market")
    env = None
    try:
        env = (report.get("risk_config") or {}).get("gateway_env")
    except Exception:
        env = None
    account_last4 = None
    msg = report.get("account_message") or ""
    m = re.search(r"uni_last4=(\w+)", msg)
    if m:
        account_last4 = m.group(1)
    else:
        m2 = re.search(r"acc_id=(\d+)", msg)
        if m2:
            account_last4 = m2.group(1)[-4:]
    return {"env": env, "account_last4": account_last4, "market": market}


def fingerprint_mismatch(anchor: dict[str, Any], fp: dict[str, str | None]) -> str | None:
    """Return a short reason string when ``anchor`` and current ``fp`` disagree
    on env/account/market; otherwise ``None``. Missing fields are ignored.
    """
    for key in ("env", "account_last4", "market"):
        a_val = anchor.get(key)
        c_val = fp.get(key)
        if a_val is None or c_val is None:
            continue
        if str(a_val) != str(c_val):
            return f"{key}:{a_val}->{c_val}"
    return None


def append_loop_log(log_stem: str, record: dict[str, Any]) -> None:
    """Append ``record`` as a single JSON line to
    ``state/runs/loop_<log_stem>_<YYYYMMDD>.jsonl``.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = now_bj().strftime("%Y%m%d")
    log_file = LOG_DIR / f"loop_{log_stem}_{date_str}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# Signal handling / graceful shutdown
# ---------------------------------------------------------------------------
@dataclass
class LoopSignalState:
    """Small container used by both loop scripts to share the active child
    process handle with a SIGINT/SIGTERM handler.

    Usage::

        state = LoopSignalState()
        install_signal_handlers(state)
        ...
        state.child_proc = subprocess.Popen(cmd)
        try:
            state.child_proc.wait(timeout=...)
        finally:
            state.child_proc = None

        if state.stop_requested:
            break
    """
    child_proc: subprocess.Popen | None = None
    stop_requested: bool = False


def install_signal_handlers(state: LoopSignalState) -> None:
    """Install SIGINT/SIGTERM handlers that flip ``stop_requested`` and
    terminate the currently-running child process so the outer loop exits
    within the next few seconds rather than hanging on a long ``time.sleep``.
    """
    def _handler(signum, frame):  # noqa: ARG001
        state.stop_requested = True
        proc = state.child_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def run_child(cmd: list[str], *, timeout: int,
              state: LoopSignalState,
              cwd: Path | str | None = None) -> tuple[int | None, bool, int]:
    """Spawn ``cmd`` as a subprocess, wait up to ``timeout`` seconds, and make
    sure the child is *not* orphaned even on timeout or signal. Returns
    ``(exit_code, timed_out, duration_ms)``.
    """
    t0 = time.time()
    timed_out = False
    exit_code: int | None = None
    state.child_proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None)
    try:
        try:
            exit_code = state.child_proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                state.child_proc.terminate()
            except Exception:
                pass
            try:
                exit_code = state.child_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    state.child_proc.kill()
                except Exception:
                    pass
                try:
                    exit_code = state.child_proc.wait(timeout=5)
                except Exception:
                    exit_code = -9
    finally:
        # Double-check: if we got a SIGINT mid-wait, orphaning the child
        # would silently leave it running after run_loop exits.
        if state.child_proc is not None and state.child_proc.poll() is None:
            try:
                state.child_proc.kill()
                state.child_proc.wait(timeout=5)
            except Exception:
                pass
        state.child_proc = None
    duration_ms = int((time.time() - t0) * 1000)
    return exit_code, timed_out, duration_ms


def sleep_responsive(total_seconds: float, state: LoopSignalState,
                     *, chunk_seconds: float = 5.0) -> None:
    """Sleep up to ``total_seconds`` in small chunks so SIGINT/SIGTERM are
    honored within ``chunk_seconds``.
    """
    slept = 0.0
    while slept < total_seconds and not state.stop_requested:
        chunk = min(chunk_seconds, total_seconds - slept)
        if chunk <= 0:
            break
        time.sleep(chunk)
        slept += chunk


__all__ = [
    "BEIJING_TZ",
    "LOG_DIR",
    "REPO_ROOT",
    "LoopSignalState",
    "anchor_path_for_portfolio",
    "anchor_path_for_symbol",
    "append_loop_log",
    "extract_env_fingerprint",
    "fingerprint_mismatch",
    "in_session",
    "install_signal_handlers",
    "now_bj",
    "parse_hhmm",
    "read_current_nav",
    "report_path_for",
    "run_child",
    "seconds_until",
    "sleep_responsive",
]
