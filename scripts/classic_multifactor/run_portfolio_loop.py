from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_PORTFOLIO_PY = REPO_ROOT / "scripts" / "classic_multifactor" / "run_portfolio.py"
LOG_DIR = REPO_ROOT / "state" / "runs"
CONFIG_ROOT = REPO_ROOT / "configs" / "classic_multifactor"

BEIJING_TZ = timezone(timedelta(hours=8))

_child_proc: subprocess.Popen | None = None
_stop_requested = False


def _parse_hhmm(s: str) -> dtime:
    hh, mm = s.split(":")
    return dtime(int(hh), int(mm))


def _now_bj() -> datetime:
    return datetime.now(tz=BEIJING_TZ)


def _in_session(now: datetime, start: dtime, end: dtime) -> bool:
    cur = now.timetz().replace(tzinfo=None)
    cur_t = dtime(cur.hour, cur.minute, cur.second)
    if start <= end:
        return start <= cur_t < end
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


def _load_portfolio(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"portfolio not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not (data.get("symbols") or []):
        raise ValueError(f"invalid portfolio: {path}")
    return data


def _report_path_for(symbol: str) -> Path:
    return LOG_DIR / f"classic_multifactor_{symbol.replace('.', '_')}_live_report.json"


def _anchor_path_for(symbol: str) -> Path:
    tag = f"classic_multifactor_{symbol.replace('.', '_')}"
    return LOG_DIR / f"loop_anchor_{tag}.json"


def _portfolio_anchor_path(portfolio_name: str) -> Path:
    return LOG_DIR / f"loop_anchor_portfolio_{portfolio_name}.json"


def _extract_env_fingerprint(report: dict[str, Any] | None) -> dict[str, str | None]:
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


def _read_current_nav(symbol: str) -> tuple[float | None, float | None, dict[str, Any] | None]:
    path = _report_path_for(symbol)
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


def _fingerprint_mismatch(anchor: dict[str, Any], fp: dict[str, str | None]) -> str | None:
    for key in ("env", "account_last4", "market"):
        a_val = anchor.get(key)
        c_val = fp.get(key)
        if a_val is None or c_val is None:
            continue
        if str(a_val) != str(c_val):
            return f"{key}:{a_val}->{c_val}"
    return None


def _load_or_build_portfolio_anchor(portfolio_name: str, today_str: str,
                                    nav_map: dict[str, float | None],
                                    fp_map: dict[str, dict[str, str | None]],
                                    force_reset: bool) -> tuple[dict[str, Any] | None, bool, str | None]:
    """Portfolio-level anchor: records per-symbol initial NAV and aggregate.

    A portfolio anchor is built only when every symbol has a readable NAV for today;
    otherwise returns (None, False, reason) so the caller can skip the breaker.
    """
    path = _portfolio_anchor_path(portfolio_name)
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
        per = anchor.get("per_symbol") or {}
        for sym, fp in fp_map.items():
            sym_anchor = per.get(sym) or {}
            mismatch = _fingerprint_mismatch(sym_anchor, fp)
            if mismatch is not None:
                stale_reason = f"env_mismatch[{sym}]:{mismatch}"
                anchor = None
                break

    just_rebuilt = False
    if anchor is None:
        # require all symbols have nav
        missing = [sym for sym, nav in nav_map.items() if nav is None]
        if missing:
            return None, False, stale_reason or f"missing_nav:{','.join(missing)}"
        per_symbol = {}
        agg_initial = 0.0
        for sym, nav in nav_map.items():
            per_symbol[sym] = {
                "initial_nav": nav,
                "env": fp_map.get(sym, {}).get("env"),
                "account_last4": fp_map.get(sym, {}).get("account_last4"),
                "market": fp_map.get(sym, {}).get("market"),
            }
            agg_initial += float(nav or 0.0)
        anchor = {
            "date": today_str,
            "portfolio_name": portfolio_name,
            "aggregate_initial_nav": agg_initial,
            "per_symbol": per_symbol,
            "created_ts": _now_bj().isoformat(),
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(anchor, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        just_rebuilt = True

    return anchor, just_rebuilt, stale_reason


def _append_loop_log(portfolio_name: str, record: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = _now_bj().strftime("%Y%m%d")
    log_file = LOG_DIR / f"loop_portfolio_{portfolio_name}_{date_str}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Periodic portfolio runner (Beijing time session).")
    p.add_argument("--portfolio", required=True, help="portfolio JSON path")

    # loop control
    p.add_argument("--interval-seconds", type=int, default=300)
    p.add_argument("--session-start", default="22:30", help="北京时间 HH:MM")
    p.add_argument("--session-end", default="05:00", help="北京时间 HH:MM（次日）")
    p.add_argument("--max-iterations", type=int, default=0)
    p.add_argument("--per-iteration-timeout", type=int, default=600,
                   help="portfolio 单轮总超时（秒），需 >= symbols * per-symbol timeout")
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--per-symbol-timeout", type=int, default=None)
    p.add_argument("--on-symbol-error", choices=["continue", "stop"], default=None)
    p.add_argument("--on-error", choices=["continue", "stop"], default="continue",
                   help="子进程（run_portfolio.py）整体失败时的处理策略")
    p.add_argument("--dry-run-loop", action="store_true")

    # safety overrides passthrough
    p.add_argument("--override-simulate", action="store_true", default=False)
    p.add_argument("--override-live-submit", action="store_true", default=False)
    p.add_argument("--override-no-live-submit", action="store_true", default=False)

    # circuit breakers
    p.add_argument("--daily-loss-limit", type=float, default=0.0,
                   help="portfolio 合计亏损美元上限，>0 触发熔断；0 表示关闭")
    p.add_argument("--per-symbol-loss-limit", type=float, default=0.0,
                   help="单标亏损美元上限，>0 触发熔断；0 表示关闭")
    p.add_argument("--anchor-reset", action="store_true",
                   help="强制重建当日 portfolio NAV 锚点")
    return p


def _build_child_cmd(args) -> list[str]:
    cmd = [sys.executable, str(RUN_PORTFOLIO_PY),
           "--portfolio", args.portfolio]
    if args.concurrency is not None:
        cmd += ["--concurrency", str(args.concurrency)]
    if args.per_symbol_timeout is not None:
        cmd += ["--per-symbol-timeout", str(args.per_symbol_timeout)]
    if args.on_symbol_error is not None:
        cmd += ["--on-symbol-error", args.on_symbol_error]
    if args.dry_run_loop:
        cmd.append("--dry-run")
    if args.override_simulate:
        cmd.append("--override-simulate")
    if args.override_live_submit:
        cmd.append("--override-live-submit")
    if args.override_no_live_submit:
        cmd.append("--override-no-live-submit")
    return cmd


def main() -> int:
    global _child_proc
    args = build_parser().parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    portfolio_path = Path(args.portfolio)
    if not portfolio_path.is_absolute():
        portfolio_path = (REPO_ROOT / portfolio_path).resolve()
    portfolio = _load_portfolio(portfolio_path)
    portfolio_name = portfolio.get("portfolio_name") or portfolio_path.stem
    symbols = [item["symbol"] for item in portfolio.get("symbols", [])]

    session_start = _parse_hhmm(args.session_start)
    session_end = _parse_hhmm(args.session_end)

    print(f"[loop-portfolio] name={portfolio_name} symbols={symbols} "
          f"interval={args.interval_seconds}s session(BJ)={args.session_start}->{args.session_end} "
          f"daily_loss_limit=${args.daily_loss_limit} per_symbol_loss_limit=${args.per_symbol_loss_limit} "
          f"override_simulate={args.override_simulate} override_live_submit={args.override_live_submit} "
          f"dry_run_loop={args.dry_run_loop}", flush=True)

    iteration = 0
    while not _stop_requested:
        now = _now_bj()

        if not _in_session(now, session_start, session_end):
            wait_s = _seconds_until(now, session_start)
            sleep_chunk = min(wait_s, 60.0)
            print(f"[loop-portfolio] out-of-session now(BJ)={now.strftime('%H:%M:%S')} "
                  f"sleep {sleep_chunk:.0f}s (until {args.session_start})", flush=True)
            time.sleep(max(1.0, sleep_chunk))
            continue

        iteration += 1
        if args.max_iterations and iteration > args.max_iterations:
            print(f"[loop-portfolio] reached max_iterations={args.max_iterations}, exit", flush=True)
            break

        cmd = _build_child_cmd(args)
        print(f"[loop-portfolio] iter={iteration} ts={now.isoformat()} cmd={' '.join(cmd)}", flush=True)

        exit_code: int | None = None
        duration_ms = 0
        timed_out = False
        t0 = time.time()
        try:
            _child_proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT))
            try:
                exit_code = _child_proc.wait(timeout=args.per_iteration_timeout)
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

        # gather per-symbol NAV & fingerprint
        today_str = _now_bj().strftime("%Y-%m-%d")
        nav_map: dict[str, float | None] = {}
        cash_map: dict[str, float | None] = {}
        fp_map: dict[str, dict[str, str | None]] = {}
        for sym in symbols:
            nav, cash, report = _read_current_nav(sym)
            nav_map[sym] = nav
            cash_map[sym] = cash
            fp_map[sym] = _extract_env_fingerprint(report)

        anchor, just_rebuilt, stale_reason = _load_or_build_portfolio_anchor(
            portfolio_name, today_str, nav_map, fp_map,
            force_reset=(args.anchor_reset and iteration == 1),
        )
        if stale_reason:
            print(f"[loop-portfolio] anchor stale ({stale_reason}), rebuilt={just_rebuilt}", flush=True)

        # per-symbol and aggregate loss
        per_symbol_loss: dict[str, float | None] = {}
        aggregate_initial: float | None = None
        aggregate_current: float | None = None
        aggregate_loss: float | None = None
        tripped_symbols: list[str] = []

        if anchor is not None:
            per = anchor.get("per_symbol") or {}
            agg_init_sum = 0.0
            agg_cur_sum = 0.0
            for sym in symbols:
                init = per.get(sym, {}).get("initial_nav")
                cur = nav_map.get(sym)
                if init is not None and cur is not None:
                    loss = max(0.0, float(init) - float(cur))
                    per_symbol_loss[sym] = loss
                    agg_init_sum += float(init)
                    agg_cur_sum += float(cur)
                    if (not just_rebuilt
                            and args.per_symbol_loss_limit > 0
                            and loss >= args.per_symbol_loss_limit):
                        tripped_symbols.append(sym)
                else:
                    per_symbol_loss[sym] = None
            if agg_init_sum > 0:
                aggregate_initial = agg_init_sum
                aggregate_current = agg_cur_sum
                aggregate_loss = max(0.0, agg_init_sum - agg_cur_sum)

        aggregate_tripped = (
            not just_rebuilt
            and args.daily_loss_limit > 0
            and aggregate_loss is not None
            and aggregate_loss >= args.daily_loss_limit
        )
        per_symbol_tripped = (not just_rebuilt) and bool(tripped_symbols)

        record = {
            "ts": _now_bj().isoformat(),
            "iteration": iteration,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "portfolio_name": portfolio_name,
            "portfolio_path": str(portfolio_path),
            "symbols": symbols,
            "nav_map": nav_map,
            "cash_map": cash_map,
            "fp_map": fp_map,
            "anchor_date": (anchor or {}).get("date"),
            "anchor_just_rebuilt": just_rebuilt,
            "anchor_stale_reason": stale_reason,
            "aggregate_initial_nav": aggregate_initial,
            "aggregate_current_nav": aggregate_current,
            "aggregate_realized_loss_usd": aggregate_loss,
            "per_symbol_realized_loss_usd": per_symbol_loss,
            "daily_loss_limit": args.daily_loss_limit,
            "per_symbol_loss_limit": args.per_symbol_loss_limit,
            "aggregate_tripped": aggregate_tripped,
            "tripped_symbols": tripped_symbols,
            "dry_run_loop": args.dry_run_loop,
        }
        _append_loop_log(portfolio_name, record)

        print(f"[loop-portfolio] iter={iteration} exit={exit_code} timed_out={timed_out} "
              f"agg_init={aggregate_initial} agg_cur={aggregate_current} agg_loss={aggregate_loss} "
              f"tripped_agg={aggregate_tripped} tripped_symbols={tripped_symbols}", flush=True)

        if aggregate_tripped:
            assert aggregate_loss is not None
            print(f"[loop-portfolio] aggregate daily-loss circuit breaker tripped "
                  f"(agg_loss={aggregate_loss:.2f} >= {args.daily_loss_limit}), stop", flush=True)
            break

        if per_symbol_tripped:
            print(f"[loop-portfolio] per-symbol loss breaker tripped symbols={tripped_symbols} "
                  f"(limit={args.per_symbol_loss_limit}), stop", flush=True)
            break

        if exit_code != 0 and args.on_error == "stop":
            print(f"[loop-portfolio] child failed exit={exit_code}, on-error=stop", flush=True)
            break

        slept = 0
        while slept < args.interval_seconds and not _stop_requested:
            chunk = min(5, args.interval_seconds - slept)
            time.sleep(chunk)
            slept += chunk

    print("[loop-portfolio] exit", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
