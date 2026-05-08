from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_PY = REPO_ROOT / "scripts" / "classic_multifactor" / "run.py"
LOG_DIR = REPO_ROOT / "state" / "runs"

BEIJING_TZ = timezone(timedelta(hours=8))

_child_proc: subprocess.Popen | None = None
_stop_requested = False


def _parse_hhmm(s: str) -> dtime:
    hh, mm = s.split(":")
    return dtime(int(hh), int(mm))


def _now_bj() -> datetime:
    return datetime.now(tz=BEIJING_TZ)


def _in_session(now: datetime, start: dtime, end: dtime) -> bool:
    """Supports cross-midnight session window (e.g. 22:30 -> 05:00)."""
    cur = now.timetz().replace(tzinfo=None)
    cur_t = dtime(cur.hour, cur.minute, cur.second)
    if start <= end:
        return start <= cur_t < end
    # cross midnight
    return cur_t >= start or cur_t < end


def _seconds_until(now: datetime, target: dtime) -> float:
    target_dt = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if target_dt <= now:
        target_dt += timedelta(days=1)
    return (target_dt - now).total_seconds()


def _handle_signal(signum, frame):
    global _stop_requested
    _stop_requested = True
    if _child_proc is not None and _child_proc.poll() is None:
        try:
            _child_proc.terminate()
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Periodic runner for run.py live mode (Beijing time session).")
    # passthrough to run.py live
    p.add_argument("--symbol", required=True)
    p.add_argument("--config", default="nvda_g09.json")
    p.add_argument("--budget-per-trade", type=float, default=3000.0)
    p.add_argument("--max-order-value", type=float, default=1000.0)
    p.add_argument("--max-candidates", type=int, default=1)
    p.add_argument("--max-selected", type=int, default=4)
    p.add_argument("--reconciliation-max-age", type=int, default=60)
    p.add_argument("--limit-price-buffer-pct", type=float, default=0.0)
    p.add_argument("--no-approval-required", action="store_true")
    p.add_argument("--simulate", action="store_true")
    p.add_argument("--minute-profile", action="store_true")
    p.add_argument("--live-submit", action="store_true")

    # loop control (Beijing time)
    p.add_argument("--interval-seconds", type=int, default=300)
    p.add_argument("--session-start", default="22:30", help="北京时间 HH:MM，默认美股盘开始")
    p.add_argument("--session-end", default="05:00", help="北京时间 HH:MM，默认美股盘结束（次日）")
    p.add_argument("--max-iterations", type=int, default=0, help="0 表示不限")
    p.add_argument("--per-run-timeout", type=int, default=180)
    p.add_argument("--on-error", choices=["continue", "stop"], default="continue")
    p.add_argument("--dry-run-loop", action="store_true", help="只打印调度节拍，不启动子进程")

    # daily circuit breaker
    p.add_argument("--daily-loss-limit", type=float, default=200.0,
                   help="单日累计亏损美元上限，>0 触发熔断；0 表示关闭")
    return p


def _build_child_cmd(args) -> list[str]:
    cmd = [sys.executable, str(RUN_PY), "live",
           "--symbol", args.symbol,
           "--config", args.config,
           "--budget-per-trade", str(args.budget_per_trade),
           "--max-order-value", str(args.max_order_value),
           "--max-candidates", str(args.max_candidates),
           "--max-selected", str(args.max_selected),
           "--reconciliation-max-age", str(args.reconciliation_max_age),
           "--limit-price-buffer-pct", str(args.limit_price_buffer_pct)]
    if args.no_approval_required:
        cmd.append("--no-approval-required")
    if args.simulate:
        cmd.append("--simulate")
    if args.minute_profile:
        cmd.append("--minute-profile")
    if args.live_submit:
        cmd.append("--live-submit")
    return cmd


def _report_path_for(symbol: str) -> Path:
    return REPO_ROOT / "state" / "runs" / f"classic_multifactor_{symbol.replace('.', '_')}_live_report.json"


def _read_drawdown_usd(symbol: str) -> tuple[float | None, dict[str, Any] | None]:
    path = _report_path_for(symbol)
    if not path.exists():
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, None
    selected = data.get("selected") or []
    if not selected:
        return None, data
    rc = (selected[0] or {}).get("risk_context") or {}
    dd_pct = rc.get("current_drawdown_pct")
    nav = rc.get("total_nav")
    if dd_pct is None or nav is None:
        return None, data
    try:
        return float(dd_pct) * float(nav), data
    except Exception:
        return None, data


def _append_loop_log(task_tag: str, record: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = _now_bj().strftime("%Y%m%d")
    log_file = LOG_DIR / f"loop_{task_tag}_{date_str}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    global _child_proc
    args = build_parser().parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    session_start = _parse_hhmm(args.session_start)
    session_end = _parse_hhmm(args.session_end)
    task_tag = f"classic_multifactor_{args.symbol.replace('.', '_')}"

    print(f"[loop] symbol={args.symbol} interval={args.interval_seconds}s "
          f"session(BJ)={args.session_start}->{args.session_end} "
          f"daily_loss_limit=${args.daily_loss_limit} "
          f"live_submit={args.live_submit} simulate={args.simulate} "
          f"dry_run_loop={args.dry_run_loop}", flush=True)

    iteration = 0
    while not _stop_requested:
        now = _now_bj()

        if not _in_session(now, session_start, session_end):
            wait_s = _seconds_until(now, session_start)
            # 限幅一次最多睡 60s，便于尽快响应 SIGINT 与重新评估
            sleep_chunk = min(wait_s, 60.0)
            print(f"[loop] out-of-session now(BJ)={now.strftime('%H:%M:%S')} "
                  f"sleep {sleep_chunk:.0f}s (until {args.session_start})", flush=True)
            time.sleep(max(1.0, sleep_chunk))
            continue

        iteration += 1
        if args.max_iterations and iteration > args.max_iterations:
            print(f"[loop] reached max_iterations={args.max_iterations}, exit", flush=True)
            break

        cmd = _build_child_cmd(args)
        print(f"[loop] iter={iteration} ts={now.isoformat()} cmd={' '.join(cmd)}", flush=True)

        exit_code: int | None = None
        duration_ms = 0
        timed_out = False

        if args.dry_run_loop:
            exit_code = 0
        else:
            t0 = time.time()
            try:
                _child_proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT))
                try:
                    exit_code = _child_proc.wait(timeout=args.per_run_timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _child_proc.terminate()
                    try:
                        exit_code = _child_proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        _child_proc.kill()
                        exit_code = _child_proc.wait()
            finally:
                duration_ms = int((time.time() - t0) * 1000)
                _child_proc = None

        # circuit breaker: daily loss
        breaker_tripped = False
        dd_usd, _ = _read_drawdown_usd(args.symbol)
        if args.daily_loss_limit > 0 and dd_usd is not None and dd_usd >= args.daily_loss_limit:
            breaker_tripped = True

        record = {
            "ts": _now_bj().isoformat(),
            "iteration": iteration,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "report_path": str(_report_path_for(args.symbol)),
            "drawdown_usd": dd_usd,
            "daily_loss_limit": args.daily_loss_limit,
            "breaker_tripped": breaker_tripped,
            "dry_run_loop": args.dry_run_loop,
        }
        _append_loop_log(task_tag, record)
        print(f"[loop] iter={iteration} exit={exit_code} timed_out={timed_out} "
              f"dd_usd={dd_usd} tripped={breaker_tripped}", flush=True)

        if breaker_tripped:
            print(f"[loop] daily-loss circuit breaker tripped "
                  f"(dd_usd={dd_usd:.2f} >= {args.daily_loss_limit}), stop", flush=True)
            break

        if exit_code != 0 and args.on_error == "stop":
            print(f"[loop] child failed exit={exit_code}, on-error=stop", flush=True)
            break

        # sleep in chunks to stay responsive to signals
        slept = 0
        while slept < args.interval_seconds and not _stop_requested:
            chunk = min(5, args.interval_seconds - slept)
            time.sleep(chunk)
            slept += chunk

    print("[loop] exit", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
