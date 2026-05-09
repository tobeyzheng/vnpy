from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Allow ``python3 scripts/classic_multifactor/run_portfolio_loop.py ...``
# (bare-path invocation) to still import sibling modules.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.classic_multifactor._loop_common import (
    LOG_DIR,
    REPO_ROOT,
    LoopSignalState,
    anchor_path_for_portfolio,
    append_loop_log,
    extract_env_fingerprint,
    fingerprint_mismatch,
    in_session,
    install_signal_handlers,
    now_bj,
    parse_hhmm,
    read_current_nav,
    run_child,
    seconds_until,
    sleep_responsive,
)

RUN_PORTFOLIO_PY = REPO_ROOT / "scripts" / "classic_multifactor" / "run_portfolio.py"
CONFIG_ROOT = REPO_ROOT / "configs" / "classic_multifactor"


def _load_portfolio(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"portfolio not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not (data.get("symbols") or []):
        raise ValueError(f"invalid portfolio: {path}")
    return data


def _load_or_build_portfolio_anchor(portfolio_name: str, today_str: str,
                                    nav_map: dict[str, float | None],
                                    fp_map: dict[str, dict[str, str | None]],
                                    force_reset: bool) -> tuple[dict[str, Any] | None, bool, str | None]:
    """Portfolio-level anchor: per-symbol initial NAV + aggregate. Builds only
    when every symbol has a readable NAV today; otherwise returns
    ``(None, False, reason)`` so the caller skips the breaker.
    """
    path = anchor_path_for_portfolio(portfolio_name)
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
            mismatch = fingerprint_mismatch(sym_anchor, fp)
            if mismatch is not None:
                stale_reason = f"env_mismatch[{sym}]:{mismatch}"
                anchor = None
                break

    just_rebuilt = False
    if anchor is None:
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
            "created_ts": now_bj().isoformat(),
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(anchor, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        just_rebuilt = True

    return anchor, just_rebuilt, stale_reason


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Periodic portfolio runner (Beijing time session).")
    p.add_argument("--portfolio", required=True, help="portfolio JSON path")

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

    p.add_argument("--override-simulate", action="store_true", default=False)
    p.add_argument("--override-live-submit", action="store_true", default=False)
    p.add_argument("--override-no-live-submit", action="store_true", default=False)

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
    args = build_parser().parse_args()

    state = LoopSignalState()
    install_signal_handlers(state)

    portfolio_path = Path(args.portfolio)
    if not portfolio_path.is_absolute():
        portfolio_path = (REPO_ROOT / portfolio_path).resolve()
    portfolio = _load_portfolio(portfolio_path)
    portfolio_name = portfolio.get("portfolio_name") or portfolio_path.stem
    symbols = [item["symbol"] for item in portfolio.get("symbols", [])]

    session_start = parse_hhmm(args.session_start)
    session_end = parse_hhmm(args.session_end)

    print(f"[loop-portfolio] name={portfolio_name} symbols={symbols} "
          f"interval={args.interval_seconds}s session(BJ)={args.session_start}->{args.session_end} "
          f"daily_loss_limit=${args.daily_loss_limit} per_symbol_loss_limit=${args.per_symbol_loss_limit} "
          f"override_simulate={args.override_simulate} override_live_submit={args.override_live_submit} "
          f"dry_run_loop={args.dry_run_loop}", flush=True)

    iteration = 0
    while not state.stop_requested:
        now = now_bj()

        if not in_session(now, session_start, session_end):
            wait_s = seconds_until(now, session_start)
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

        exit_code, timed_out, duration_ms = run_child(
            cmd, timeout=args.per_iteration_timeout, state=state, cwd=REPO_ROOT
        )

        today_str = now_bj().strftime("%Y-%m-%d")
        nav_map: dict[str, float | None] = {}
        cash_map: dict[str, float | None] = {}
        fp_map: dict[str, dict[str, str | None]] = {}
        for sym in symbols:
            nav, cash, report = read_current_nav(sym)
            nav_map[sym] = nav
            cash_map[sym] = cash
            fp_map[sym] = extract_env_fingerprint(report)

        anchor, just_rebuilt, stale_reason = _load_or_build_portfolio_anchor(
            portfolio_name, today_str, nav_map, fp_map,
            force_reset=(args.anchor_reset and iteration == 1),
        )
        if stale_reason:
            print(f"[loop-portfolio] anchor stale ({stale_reason}), rebuilt={just_rebuilt}", flush=True)

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
            "ts": now_bj().isoformat(),
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
        append_loop_log(f"portfolio_{portfolio_name}", record)

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

        sleep_responsive(args.interval_seconds, state)

    print("[loop-portfolio] exit", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
