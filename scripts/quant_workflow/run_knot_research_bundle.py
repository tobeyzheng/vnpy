"""Sequential and weekday-scheduled runner for Knot research workflows.

Immediate mode runs, in order:
1. HK 4-dimension picks
2. US 4-dimension picks
3. Holdings directional review

Schedule mode (``--schedule-workdays``) keeps the process alive and triggers:
- HK picks at 09:00 Asia/Hong_Kong on weekdays
- HK holdings review at 09:00 and 12:00 Asia/Hong_Kong on weekdays
- US picks at 09:00 America/New_York on weekdays
- US holdings review at 09:00 and 12:00 America/New_York on weekdays

By default JSON outputs are written under ``log/YYYYMMDDHH/``.

Side-effects:
- HK / US picks: outbound Knot LLM calls only.
- Holdings review: read-only OpenD position query against the requested
  trading environment (default REAL) + outbound Knot LLM call.
- No orders are submitted.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.strategy.knot_pick_helpers import (  # noqa: E402
    build_default_output_relpath,
    build_log_hour_key,
)
from services.strategy.market_rules import market_timezone  # noqa: E402


@dataclass(frozen=True)
class BundleTask:
    task_id: str
    label: str
    script_name: str
    filename: str
    market: str | None = None
    run_at: str | None = None


IMMEDIATE_TASKS: tuple[BundleTask, ...] = (
    BundleTask("hk_picks", "HK 4-dimension picks", "run_knot_4dim_picks_hk.py", "knot_4dim_hk.json"),
    BundleTask("us_picks", "US 4-dimension picks", "run_knot_4dim_picks_us.py", "knot_4dim_us.json"),
    BundleTask("holdings_review", "Holdings directional review", "run_holdings_knot_review.py", "holdings_knot_review.json"),
)

SCHEDULED_TASKS: tuple[BundleTask, ...] = (
    BundleTask("hk_picks", "HK 4-dimension picks", "run_knot_4dim_picks_hk.py", "knot_4dim_hk.json", market="hong_kong", run_at="09:00"),
    BundleTask("holdings_hk", "Holdings review before HK open", "run_holdings_knot_review.py", "holdings_knot_review.json", market="hong_kong", run_at="09:00"),
    BundleTask("holdings_hk", "Holdings review before HK open", "run_holdings_knot_review.py", "holdings_knot_review.json", market="hong_kong", run_at="10:40"),
    BundleTask("holdings_hk_noon", "Holdings review at HK midday", "run_holdings_knot_review.py", "holdings_knot_review.json", market="hong_kong", run_at="12:00"),
    BundleTask("holdings_hk_noon", "Holdings review at HK midday", "run_holdings_knot_review.py", "holdings_knot_review.json", market="hong_kong", run_at="13:40"),
    BundleTask("us_picks", "US 4-dimension picks", "run_knot_4dim_picks_us.py", "knot_4dim_us.json", market="us", run_at="09:00"),
    BundleTask("holdings_us", "Holdings review before US open", "run_holdings_knot_review.py", "holdings_knot_review.json", market="us", run_at="09:00"),
    BundleTask("holdings_us_noon", "Holdings review at US midday", "run_holdings_knot_review.py", "holdings_knot_review.json", market="us", run_at="12:00"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Knot research workflows immediately or on weekday market schedules."
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional shared output directory. Defaults to log/YYYYMMDDHH under the repo root.",
    )
    parser.add_argument(
        "--per-dim",
        type=int,
        default=None,
        help="Optional shared --per-dim value forwarded to the HK/US pick scripts.",
    )
    parser.add_argument(
        "--skip-knot",
        action="store_true",
        help="Forward --skip-knot to the holdings review task only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Forward --dry-run to all three tasks so they print only and do not write JSON files.",
    )
    parser.add_argument(
        "--holdings-trd-env",
        default="REAL",
        choices=("REAL", "SIMULATE"),
        help="Trading environment forwarded to holdings review. Defaults to REAL (read-only query only).",
    )
    parser.set_defaults(holdings_live_strict=True)
    parser.add_argument(
        "--holdings-live-strict",
        dest="holdings_live_strict",
        action="store_true",
        help="Use strict account selection for holdings review (default: enabled).",
    )
    parser.add_argument(
        "--no-holdings-live-strict",
        dest="holdings_live_strict",
        action="store_false",
        help="Disable strict account selection for holdings review.",
    )
    parser.add_argument(
        "--schedule-workdays",
        action="store_true",
        help="Keep running and trigger market-local weekday schedules (HK 09:00/12:00 HKT holdings, US 09:00/12:00 ET holdings, plus HK/US picks at 09:00 ET/HKT).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=30.0,
        help="Scheduler polling interval in seconds when --schedule-workdays is enabled.",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=300.0,
        help="Scheduler heartbeat interval in seconds when --schedule-workdays is enabled.",
    )
    parser.add_argument(
        "--schedule-window-minutes",
        type=int,
        default=60,
        help="How long after the scheduled local time a task is still eligible to run on startup/restart.",
    )
    return parser


def _resolve_output_dir(output_dir: str | None, *, hour_key: str) -> Path:
    raw = str(output_dir or "").strip()
    chosen = Path(raw) if raw else Path("log") / hour_key
    return chosen if chosen.is_absolute() else REPO_ROOT / chosen


def _build_command(
    *,
    script_name: str,
    filename: str,
    output_dir: Path,
    per_dim: int | None,
    skip_knot: bool,
    dry_run: bool,
    holdings_trd_env: str,
    holdings_live_strict: bool,
) -> list[str]:
    command = [sys.executable, str(REPO_ROOT / "scripts" / "quant_workflow" / script_name)]
    if script_name != "run_holdings_knot_review.py" and per_dim is not None:
        command.extend(["--per-dim", str(int(per_dim))])
    if script_name == "run_holdings_knot_review.py":
        command.extend(["--trd-env", str(holdings_trd_env).upper()])
        if holdings_live_strict:
            command.append("--live-strict")
        if skip_knot:
            command.append("--skip-knot")
    if not dry_run:
        command.extend(["--output", str(output_dir / filename)])
    else:
        command.append("--dry-run")
    return command


def _run_command(command: list[str], *, script_name: str) -> int:
    started_at = time.monotonic()
    last_heartbeat = started_at
    with subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        while True:
            line = proc.stdout.readline() if proc.stdout is not None else ""
            if line:
                print(line, end="")
                last_heartbeat = time.monotonic()
                continue

            returncode = proc.poll()
            now = time.monotonic()
            if now - last_heartbeat >= 5:
                elapsed = int(now - started_at)
                print(f"[knot_research_bundle] {script_name} still running... elapsed={elapsed}s")
                last_heartbeat = now
            if returncode is not None:
                return int(returncode)
            time.sleep(0.5)


def _task_timezone(task: BundleTask) -> ZoneInfo:
    market = str(task.market or "us")
    return ZoneInfo(market_timezone(market))


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = str(value).split(":", 1)
    return int(hour), int(minute)


def _scheduled_task_due(
    task: BundleTask,
    *,
    now_utc: datetime,
    executed_slots: dict[str, str],
    window_seconds: float,
) -> tuple[bool, str | None, datetime | None]:
    if not task.run_at or not task.market:
        return False, None, None
    local_now = now_utc.astimezone(_task_timezone(task))
    if local_now.weekday() >= 5:
        return False, None, local_now
    hour, minute = _parse_hhmm(task.run_at)
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delay_seconds = (local_now - target).total_seconds()
    if delay_seconds < 0 or delay_seconds > window_seconds:
        return False, None, local_now
    slot_key = f"{task.task_id}:{local_now.date().isoformat()}"
    if executed_slots.get(task.task_id) == slot_key:
        return False, slot_key, local_now
    return True, slot_key, local_now


def _run_task(task: BundleTask, args: argparse.Namespace) -> int:
    hour_key = build_log_hour_key()
    output_dir = _resolve_output_dir(args.output_dir, hour_key=hour_key)
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    effective_output = output_dir / task.filename if not args.dry_run else build_default_output_relpath(
        task.filename,
        hour_key=hour_key,
    )
    print(f"[knot_research_bundle] running {task.label} ({task.script_name}) -> {effective_output}")
    command = _build_command(
        script_name=task.script_name,
        filename=task.filename,
        output_dir=output_dir,
        per_dim=args.per_dim,
        skip_knot=bool(args.skip_knot),
        dry_run=bool(args.dry_run),
        holdings_trd_env=str(args.holdings_trd_env or "REAL").upper(),
        holdings_live_strict=bool(args.holdings_live_strict),
    )
    completed = _run_command(command, script_name=task.script_name)
    if completed != 0:
        print(
            f"[knot_research_bundle] stopped because {task.script_name} exited with {completed}",
            file=sys.stderr,
        )
    return int(completed)


def _run_immediate(args: argparse.Namespace) -> int:
    for task in IMMEDIATE_TASKS:
        completed = _run_task(task, args)
        if completed != 0:
            return completed
    print("[knot_research_bundle] all tasks completed")
    return 0


def _format_scheduler_heartbeat(executed_slots: dict[str, str]) -> str:
    hk_now = datetime.now(_task_timezone(BundleTask("hk", "", "", "", market="hong_kong")))
    us_now = datetime.now(_task_timezone(BundleTask("us", "", "", "", market="us")))
    executed = ", ".join(sorted(executed_slots.values())) or "none"
    return (
        "[knot_research_bundle] scheduler heartbeat "
        f"hk_now={hk_now.strftime('%Y-%m-%d %H:%M:%S %Z')} "
        f"us_now={us_now.strftime('%Y-%m-%d %H:%M:%S %Z')} "
        f"executed={executed}"
    )


def _run_scheduler(args: argparse.Namespace) -> int:
    executed_slots: dict[str, str] = {}
    window_seconds = max(float(args.schedule_window_minutes or 0), 0.0) * 60.0
    poll_seconds = max(float(args.poll_seconds or 0.0), 1.0)
    heartbeat_seconds = max(float(args.heartbeat_seconds or 0.0), 10.0)
    last_heartbeat = 0.0

    print("[knot_research_bundle] scheduler started")
    for task in SCHEDULED_TASKS:
        tz_name = market_timezone(str(task.market or "us"))
        print(
            f"[knot_research_bundle] schedule task={task.task_id} market={task.market} "
            f"run_at={task.run_at} timezone={tz_name}"
        )

    while True:
        now_utc = datetime.now(timezone.utc)
        for task in SCHEDULED_TASKS:
            due, slot_key, local_now = _scheduled_task_due(
                task,
                now_utc=now_utc,
                executed_slots=executed_slots,
                window_seconds=window_seconds,
            )
            if not due:
                continue
            print(
                f"[knot_research_bundle] due task={task.task_id} "
                f"local_time={local_now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
            )
            completed = _run_task(task, args)
            if completed != 0:
                return completed
            if slot_key:
                executed_slots[task.task_id] = slot_key

        monotonic_now = time.monotonic()
        if monotonic_now - last_heartbeat >= heartbeat_seconds:
            print(_format_scheduler_heartbeat(executed_slots))
            last_heartbeat = monotonic_now
        time.sleep(poll_seconds)


def main() -> int:
    args = build_parser().parse_args()
    if args.schedule_workdays:
        return _run_scheduler(args)
    return _run_immediate(args)


if __name__ == "__main__":
    raise SystemExit(main())
