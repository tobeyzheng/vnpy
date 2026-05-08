from __future__ import annotations

import argparse
import concurrent.futures as _futures
import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_PY = REPO_ROOT / "scripts" / "classic_multifactor" / "run.py"
LOG_DIR = REPO_ROOT / "state" / "runs"
CONFIG_ROOT = REPO_ROOT / "configs" / "classic_multifactor"

BEIJING_TZ = timezone(timedelta(hours=8))


def _now_bj() -> datetime:
    return datetime.now(tz=BEIJING_TZ)


def _load_portfolio(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"portfolio not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"portfolio root must be object: {path}")
    symbols = data.get("symbols") or []
    if not isinstance(symbols, list) or not symbols:
        raise ValueError(f"portfolio.symbols must be non-empty list: {path}")
    for i, item in enumerate(symbols):
        if not isinstance(item, dict) or not item.get("symbol") or not item.get("config"):
            raise ValueError(f"portfolio.symbols[{i}] must contain 'symbol' and 'config'")
        cfg_path = CONFIG_ROOT / item["config"]
        if not cfg_path.exists():
            raise FileNotFoundError(f"symbol config missing: {cfg_path}")
    return data


def _build_child_cmd(symbol_item: dict[str, Any], shared: dict[str, Any],
                     overrides: dict[str, Any]) -> list[str]:
    """Compose `python run.py live ...` command for one symbol."""
    def pick(key: str, default: Any = None) -> Any:
        if key in overrides and overrides[key] is not None:
            return overrides[key]
        if key in symbol_item:
            return symbol_item[key]
        return shared.get(key, default)

    symbol = symbol_item["symbol"]
    config = symbol_item["config"]
    cmd: list[str] = [
        sys.executable, str(RUN_PY), "live",
        "--symbol", str(symbol),
        "--config", str(config),
        "--budget-per-trade", str(pick("budget_per_trade", 3000.0)),
        "--max-order-value", str(pick("max_order_value", 1000.0)),
        "--max-candidates", str(pick("max_candidates", 1)),
        "--max-selected", str(pick("max_selected", 4)),
        "--reconciliation-max-age", str(pick("reconciliation_max_age", 60)),
        "--limit-price-buffer-pct", str(pick("limit_price_buffer_pct", 0.0)),
    ]
    if bool(pick("no_approval_required", False)):
        cmd.append("--no-approval-required")
    if bool(pick("simulate", True)):
        cmd.append("--simulate")
    if bool(pick("minute_profile", False)):
        cmd.append("--minute-profile")
    if bool(pick("live_submit", False)):
        cmd.append("--live-submit")
    return cmd


def _run_one(symbol_item: dict[str, Any], shared: dict[str, Any],
             overrides: dict[str, Any], timeout: int, dry_run: bool) -> dict[str, Any]:
    cmd = _build_child_cmd(symbol_item, shared, overrides)
    symbol = symbol_item["symbol"]
    record: dict[str, Any] = {
        "symbol": symbol,
        "config": symbol_item.get("config"),
        "cmd": " ".join(cmd),
        "exit_code": None,
        "timed_out": False,
        "duration_ms": 0,
        "report_path": str(LOG_DIR / f"classic_multifactor_{symbol.replace('.', '_')}_live_report.json"),
    }
    if dry_run:
        record["exit_code"] = 0
        record["dry_run"] = True
        return record

    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    try:
        record["exit_code"] = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
        proc.terminate()
        try:
            record["exit_code"] = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            record["exit_code"] = proc.wait()
    finally:
        record["duration_ms"] = int((time.time() - t0) * 1000)
    return record


def _summarize_report(report_path: Path) -> dict[str, Any]:
    if not report_path.exists():
        return {"report_exists": False}
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return {"report_exists": True, "report_error": str(exc)}
    selected = data.get("selected") or []
    summary: dict[str, Any] = {
        "report_exists": True,
        "market": data.get("market"),
        "selected_count": len(selected),
    }
    if selected:
        rc = (selected[0] or {}).get("risk_context") or {}
        summary["total_nav"] = rc.get("total_nav")
        summary["cash"] = rc.get("cash")
    try:
        summary["gateway_env"] = (data.get("risk_config") or {}).get("gateway_env")
    except Exception:
        summary["gateway_env"] = None
    summary["account_message"] = data.get("account_message")
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run classic_multifactor live pipeline across a portfolio of symbols.")
    p.add_argument("--portfolio", required=True, help="portfolio JSON path")
    p.add_argument("--concurrency", type=int, default=None, help="override portfolio.execution.concurrency")
    p.add_argument("--per-symbol-timeout", type=int, default=None, help="override portfolio.execution.per_symbol_timeout")
    p.add_argument("--on-symbol-error", choices=["continue", "stop"], default=None,
                   help="override portfolio.execution.on_symbol_error")
    p.add_argument("--dry-run", action="store_true", help="print child commands without spawning")

    # safety overrides (all optional; None => follow config)
    p.add_argument("--override-simulate", dest="override_simulate", action="store_true", default=None,
                   help="force simulate=true for all symbols")
    p.add_argument("--override-live-submit", dest="override_live_submit", action="store_true", default=None,
                   help="force live_submit=true for all symbols (DANGEROUS)")
    p.add_argument("--override-no-live-submit", dest="override_no_live_submit", action="store_true", default=None,
                   help="force live_submit=false for all symbols")
    return p


def main() -> int:
    args = build_parser().parse_args()
    portfolio_path = Path(args.portfolio)
    if not portfolio_path.is_absolute():
        portfolio_path = (REPO_ROOT / portfolio_path).resolve()
    portfolio = _load_portfolio(portfolio_path)

    shared: dict[str, Any] = dict(portfolio.get("shared_options") or {})
    exec_opts: dict[str, Any] = dict(portfolio.get("execution") or {})
    concurrency = int(args.concurrency if args.concurrency is not None else exec_opts.get("concurrency", 1))
    per_symbol_timeout = int(args.per_symbol_timeout if args.per_symbol_timeout is not None
                             else exec_opts.get("per_symbol_timeout", 180))
    on_symbol_error = args.on_symbol_error or exec_opts.get("on_symbol_error", "continue")

    overrides: dict[str, Any] = {}
    if args.override_simulate:
        overrides["simulate"] = True
    if args.override_live_submit:
        overrides["live_submit"] = True
    if args.override_no_live_submit:
        overrides["live_submit"] = False

    portfolio_name = portfolio.get("portfolio_name") or portfolio_path.stem
    symbols_list = list(portfolio.get("symbols") or [])

    print(f"[portfolio] name={portfolio_name} symbols={len(symbols_list)} "
          f"concurrency={concurrency} per_symbol_timeout={per_symbol_timeout}s "
          f"on_error={on_symbol_error} dry_run={args.dry_run}", flush=True)

    start_ts = _now_bj()
    results: list[dict[str, Any]] = []
    stop_flag = False

    if concurrency <= 1:
        for item in symbols_list:
            if stop_flag:
                break
            print(f"[portfolio] start symbol={item['symbol']}", flush=True)
            rec = _run_one(item, shared, overrides, per_symbol_timeout, args.dry_run)
            rec["summary"] = _summarize_report(Path(rec["report_path"]))
            results.append(rec)
            print(f"[portfolio] done symbol={item['symbol']} exit={rec['exit_code']} "
                  f"timed_out={rec['timed_out']} duration_ms={rec['duration_ms']}", flush=True)
            if rec["exit_code"] not in (0, None) and on_symbol_error == "stop":
                stop_flag = True
    else:
        with _futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = {
                pool.submit(_run_one, item, shared, overrides, per_symbol_timeout, args.dry_run): item
                for item in symbols_list
            }
            for fut in _futures.as_completed(futs):
                item = futs[fut]
                try:
                    rec = fut.result()
                except Exception as exc:
                    rec = {
                        "symbol": item.get("symbol"),
                        "config": item.get("config"),
                        "exit_code": -1,
                        "error": str(exc),
                        "timed_out": False,
                        "duration_ms": 0,
                        "report_path": str(LOG_DIR / f"classic_multifactor_{str(item.get('symbol','')).replace('.', '_')}_live_report.json"),
                    }
                rec["summary"] = _summarize_report(Path(rec["report_path"]))
                results.append(rec)
                print(f"[portfolio] done symbol={rec.get('symbol')} exit={rec.get('exit_code')} "
                      f"timed_out={rec.get('timed_out')} duration_ms={rec.get('duration_ms')}", flush=True)

    end_ts = _now_bj()
    ok = sum(1 for r in results if r.get("exit_code") == 0)
    fail = sum(1 for r in results if r.get("exit_code") not in (0, None))
    aggregate_nav: float | None = None
    try:
        navs = [float(r["summary"]["total_nav"])
                for r in results
                if isinstance(r.get("summary"), dict) and r["summary"].get("total_nav") is not None]
        aggregate_nav = sum(navs) if navs else None
    except Exception:
        aggregate_nav = None

    run_record = {
        "portfolio_name": portfolio_name,
        "portfolio_path": str(portfolio_path),
        "start_ts": start_ts.isoformat(),
        "end_ts": end_ts.isoformat(),
        "duration_ms": int((end_ts - start_ts).total_seconds() * 1000),
        "concurrency": concurrency,
        "per_symbol_timeout": per_symbol_timeout,
        "on_symbol_error": on_symbol_error,
        "dry_run": args.dry_run,
        "overrides": overrides,
        "symbols_total": len(symbols_list),
        "symbols_ok": ok,
        "symbols_failed": fail,
        "aggregate_total_nav": aggregate_nav,
        "results": results,
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts_tag = start_ts.strftime("%Y%m%d_%H%M%S")
    out_path = LOG_DIR / f"portfolio_{portfolio_name}_{ts_tag}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(run_record, f, ensure_ascii=False, indent=2, default=str)

    print(f"[portfolio] summary ok={ok} failed={fail} total={len(symbols_list)} "
          f"aggregate_nav={aggregate_nav} report={out_path}", flush=True)

    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
