from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow ``python3 scripts/classic_multifactor/run_loop.py ...`` (bare-path
# invocation) to still import sibling modules; without this patch sys.path
# would not contain the repo root and ``from scripts....`` would fail.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.classic_multifactor._loop_common import (
    LOG_DIR,
    REPO_ROOT,
    LoopSignalState,
    anchor_path_for_symbol,
    append_loop_log,
    extract_env_fingerprint,
    fingerprint_mismatch,
    in_session,
    install_signal_handlers,
    now_bj,
    parse_hhmm,
    read_current_nav,
    report_path_for,
    run_child,
    sleep_responsive,
)

RUN_PY = REPO_ROOT / "scripts" / "classic_multifactor" / "run.py"


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
    p.add_argument("--exit-after-session", action="store_true",
                   help="会话结束后直接退出 loop（默认会持续等待下一个会话）")

    # daily circuit breaker
    p.add_argument("--daily-loss-limit", type=float, default=200.0,
                   help="单日累计亏损美元上限，>0 触发熔断；0 表示关闭")
    p.add_argument("--anchor-reset", action="store_true",
                   help="强制重建当日 NAV 锚点")
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


def _load_or_build_anchor(task_tag: str, today_str: str,
                          current_nav: float | None, current_cash: float | None,
                          fp: dict[str, str | None],
                          force_reset: bool) -> tuple[dict[str, Any] | None, bool, str | None]:
    """Load anchor for today; rebuild if missing / stale / forced / env-mismatch.

    Returns (anchor, just_rebuilt, stale_reason). When ``just_rebuilt`` is True
    the caller must skip the circuit breaker for this iteration so a fresh
    anchor is never tripped on its own creation.
    """
    tag = task_tag.replace("classic_multifactor_", "", 1)
    path = anchor_path_for_symbol(tag) if not tag.startswith("classic_multifactor_") else LOG_DIR / f"loop_anchor_{task_tag}.json"
    # Keep legacy filename shape (loop_anchor_classic_multifactor_<sym>.json):
    path = LOG_DIR / f"loop_anchor_{task_tag}.json"

    anchor: dict[str, Any] | None = None
    stale_reason: str | None = None
    if path.exists() and not force_reset:
        try:
            with open(path, "r", encoding="utf-8") as f:
                anchor = json.load(f)
        except Exception:
            anchor = None
            stale_reason = "unreadable"
    elif force_reset:
        stale_reason = "force_reset"
    if anchor is not None and anchor.get("date") != today_str:
        stale_reason = f"date:{anchor.get('date')}->{today_str}"
        anchor = None
    if anchor is not None:
        mismatch = fingerprint_mismatch(anchor, fp)
        if mismatch is not None:
            stale_reason = f"env_mismatch:{mismatch}"
            anchor = None
    just_rebuilt = False
    if anchor is None:
        if current_nav is None:
            return None, False, stale_reason
        anchor = {
            "date": today_str,
            "initial_nav": current_nav,
            "initial_cash": current_cash,
            "env": fp.get("env"),
            "account_last4": fp.get("account_last4"),
            "market": fp.get("market"),
            "created_ts": now_bj().isoformat(),
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(anchor, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        just_rebuilt = True
    return anchor, just_rebuilt, stale_reason


def main() -> int:
    args = build_parser().parse_args()

    state = LoopSignalState()
    install_signal_handlers(state)

    session_start = parse_hhmm(args.session_start)
    session_end = parse_hhmm(args.session_end)
    task_tag = f"classic_multifactor_{args.symbol.replace('.', '_')}"

    print(f"[loop] symbol={args.symbol} interval={args.interval_seconds}s "
          f"session(BJ)={args.session_start}->{args.session_end} "
          f"daily_loss_limit=${args.daily_loss_limit} "
          f"live_submit={args.live_submit} simulate={args.simulate} "
          f"dry_run_loop={args.dry_run_loop}", flush=True)

    iteration = 0
    entered_session_once = False
    while not state.stop_requested:
        now = now_bj()

        if in_session(now, session_start, session_end):
            entered_session_once = True
        else:
            if entered_session_once and args.exit_after_session:
                print(f"[loop] session ended (BJ end={args.session_end}), exit-after-session requested, exit", flush=True)
                break
            from scripts.classic_multifactor._loop_common import seconds_until
            wait_s = seconds_until(now, session_start)
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
            exit_code, timed_out, duration_ms = run_child(
                cmd, timeout=args.per_run_timeout, state=state, cwd=REPO_ROOT
            )

        # circuit breaker: daily loss via NAV anchor
        today_str = now_bj().strftime("%Y-%m-%d")
        cur_nav, cur_cash, cur_report = read_current_nav(args.symbol)
        fp = extract_env_fingerprint(cur_report)
        anchor, just_rebuilt, stale_reason = _load_or_build_anchor(
            task_tag, today_str, cur_nav, cur_cash, fp,
            force_reset=(args.anchor_reset and iteration == 1),
        )
        if stale_reason:
            print(f"[loop] anchor stale ({stale_reason}), rebuilt={just_rebuilt}", flush=True)
        initial_nav = float(anchor["initial_nav"]) if anchor and anchor.get("initial_nav") is not None else None
        realized_loss_usd: float | None = None
        if initial_nav is not None and cur_nav is not None:
            realized_loss_usd = max(0.0, initial_nav - cur_nav)

        breaker_tripped = (
            not just_rebuilt
            and args.daily_loss_limit > 0
            and realized_loss_usd is not None
            and realized_loss_usd >= args.daily_loss_limit
        )

        record = {
            "ts": now_bj().isoformat(),
            "iteration": iteration,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "report_path": str(report_path_for(args.symbol)),
            "anchor_date": (anchor or {}).get("date"),
            "anchor_env": (anchor or {}).get("env"),
            "anchor_account_last4": (anchor or {}).get("account_last4"),
            "anchor_market": (anchor or {}).get("market"),
            "anchor_just_rebuilt": just_rebuilt,
            "anchor_stale_reason": stale_reason,
            "current_env": fp.get("env"),
            "current_account_last4": fp.get("account_last4"),
            "current_market": fp.get("market"),
            "initial_nav": initial_nav,
            "current_nav": cur_nav,
            "realized_loss_usd": realized_loss_usd,
            "daily_loss_limit": args.daily_loss_limit,
            "breaker_tripped": breaker_tripped,
            "dry_run_loop": args.dry_run_loop,
        }
        append_loop_log(task_tag, record)
        print(f"[loop] iter={iteration} exit={exit_code} timed_out={timed_out} "
              f"env={fp.get('env')} acct_last4={fp.get('account_last4')} "
              f"initial_nav={initial_nav} current_nav={cur_nav} "
              f"realized_loss_usd={realized_loss_usd} tripped={breaker_tripped}", flush=True)

        if breaker_tripped:
            assert realized_loss_usd is not None
            print(f"[loop] daily-loss circuit breaker tripped "
                  f"(realized_loss_usd={realized_loss_usd:.2f} >= {args.daily_loss_limit}), stop", flush=True)
            break

        if exit_code != 0 and args.on_error == "stop":
            print(f"[loop] child failed exit={exit_code}, on-error=stop", flush=True)
            break

        sleep_responsive(args.interval_seconds, state)

    print("[loop] exit", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
