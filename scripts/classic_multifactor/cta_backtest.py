from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from vnpy.trader.constant import Interval
from vnpy.trader.optimize import OptimizationSetting
from vnpy_ctastrategy.backtesting import BacktestingEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.classic_multifactor.strategy import ClassicMultiFactorCtaStrategy

DEFAULT_TARGET = "sharpe_ratio"
DEFAULT_SWEEP_DIR = REPO_ROOT / "state" / "runs" / "reports"


class ClassicCtaBacktestRunner:
    """Official vn.py CTA BacktestingEngine runner."""

    def run(
        self,
        *,
        vt_symbol: str,
        interval: str,
        start: datetime,

        end: datetime,
        capital: float,
        rate: float,
        slippage: float,
        size: int,
        pricetick: float,
        setting: dict[str, Any],
    ) -> tuple[dict[str, Any], Any]:
        # vnpy BacktestingEngine.load_data uses (end - start).days as denominator;
        # any difference < 1 day yields 0 -> ZeroDivisionError. Normalise by
        # ensuring effective_end is at least start + 1 day for single-day intent.
        from datetime import timedelta as _td
        effective_end = end
        if effective_end <= start:
            effective_end = start + _td(days=1)
        elif (effective_end - start).days < 1:
            effective_end = start + _td(days=1)
        elif effective_end.hour == 0 and effective_end.minute == 0 and effective_end.second == 0:
            effective_end = effective_end.replace(hour=23, minute=59, second=59)

        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbol=vt_symbol,
            interval=Interval.MINUTE if interval == "1m" else Interval.DAILY,

            start=start,
            end=effective_end,
            rate=rate,
            slippage=slippage,
            size=size,
            pricetick=pricetick,
            capital=int(capital),
        )
        engine.add_strategy(ClassicMultiFactorCtaStrategy, setting)
        engine.load_data()
        engine.run_backtesting()
        df = engine.calculate_result()
        if df is None or getattr(df, "empty", False):
            return {
                "status": "no_data",
                "message": "vn.py CTA backtest ran but no result was produced",
                "vt_symbol": vt_symbol,
                "start": start.isoformat(),
                "end": effective_end.isoformat(),
            }, engine
        stats = engine.calculate_statistics(df=df, output=False)
        stats = {"status": "ok", "engine": "vnpy_cta_backtesting", **stats}
        try:
            stats["trade_count_runtime"] = len(engine.get_all_trades())
        except Exception:
            pass
        return stats, engine

    def run_optimization(
        self,
        *,
        vt_symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        capital: float,
        rate: float,
        slippage: float,
        size: int,
        pricetick: float,
        base_setting: dict[str, Any],
        opt_setting: OptimizationSetting,
        mode: str = "bf",
        max_workers: int | None = None,
        ga_kwargs: dict[str, Any] | None = None,
    ) -> list[tuple]:
        """Run vnpy-native bf/ga optimization.

        ``base_setting`` supplies fixed dimensions; ``opt_setting`` supplies
        the swept dimensions and target. Swept keys override base keys.
        """
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbol=vt_symbol,
            interval=Interval.MINUTE if interval == "1m" else Interval.DAILY,
            start=start,
            end=end,
            rate=rate,
            slippage=slippage,
            size=size,
            pricetick=pricetick,
            capital=int(capital),
        )
        engine.add_strategy(ClassicMultiFactorCtaStrategy, dict(base_setting))

        if mode == "bf":
            return engine.run_bf_optimization(
                opt_setting,
                output=True,
                max_workers=max_workers,
            )
        if mode == "ga":
            ga_kwargs = dict(ga_kwargs or {})
            return engine.run_ga_optimization(
                opt_setting,
                output=True,
                max_workers=max_workers,
                **ga_kwargs,
            )
        raise ValueError(f"Unsupported optimization mode: {mode!r}")


# ---------------------------------------------------------------------------
# Helpers for CLI (single & sweep)
# ---------------------------------------------------------------------------

def load_config(path: str | Path | None) -> dict[str, Any]:
    """Load JSON config compatible with configs/classic_multifactor/*.json.

    Returns an empty dict if ``path`` is falsy.
    """
    if not path:
        return {}
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def parse_opt_param(spec: str) -> tuple[str, float, float | None, float | None]:
    """Parse ``KEY=START:END:STEP`` or ``KEY=VALUE`` spec.

    Returns (name, start, end_or_None, step_or_None).
    """
    if "=" not in spec:
        raise ValueError(f"Invalid --opt-param (missing '='): {spec!r}")
    key, _, rhs = spec.partition("=")
    key = key.strip()
    parts = [p.strip() for p in rhs.split(":")]
    if len(parts) == 1:
        return key, float(parts[0]), None, None
    if len(parts) == 3:
        start, end, step = (float(x) for x in parts)
        return key, start, end, step
    raise ValueError(
        f"Invalid --opt-param range (expect KEY=VALUE or KEY=START:END:STEP): {spec!r}"
    )


def build_opt_setting(target: str, opt_params: list[str]) -> OptimizationSetting:
    """Build vnpy OptimizationSetting from CLI ``--opt-param`` list."""
    opt = OptimizationSetting()
    opt.set_target(target)
    variable_count = 0
    for spec in opt_params:
        name, start, end, step = parse_opt_param(spec)
        ok, msg = opt.add_parameter(name, start, end, step)
        if not ok:
            raise ValueError(f"OptimizationSetting.add_parameter failed for {spec!r}: {msg}")
        if end is not None and step is not None:
            variable_count += 1
    if variable_count == 0:
        raise ValueError(
            "At least one variable --opt-param (KEY=START:END:STEP) is required for optimization"
        )
    return opt


def dump_sweep_results(
    *,
    results: list[tuple],
    output_path: Path,
    mode: str,
    target: str,
    vt_symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    base_setting: dict[str, Any],
    sweep_space: dict[str, list],
    top_n: int = 20,
) -> Path:
    """Persist sweep results as a JSON report; returns the written path."""
    # vnpy returns ``list[tuple]`` where each tuple is
    # ``(setting_dict, target_value, statistics_dict)``.
    normalized: list[dict[str, Any]] = []
    for rank, item in enumerate(results[:top_n], start=1):
        params: Any = None
        target_value: Any = None
        stats: Any = None
        try:
            params = item[0]
            target_value = item[1]
            if len(item) > 2:
                stats = item[2]
        except Exception:
            params = str(item)
        normalized.append(
            {
                "rank": rank,
                "params": params,
                "target_value": target_value,
                "stats": stats,
            }
        )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "vnpy_cta_backtesting",
        "mode": mode,
        "target": target,
        "vt_symbol": vt_symbol,
        "interval": interval,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "base_setting": base_setting,
        "sweep_space": sweep_space,
        "total_combinations": len(results),
        "top_n": top_n,
        "top_results": normalized,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Classic multifactor vn.py CTA backtest (single & bf/ga sweep). "
            "When --optimize is not set, runs a single backtest; "
            "otherwise runs vnpy-native brute-force or genetic optimization."
        )
    )
    p.add_argument("--config", default="", help="Path to JSON config (configs/classic_multifactor/*.json)")
    p.add_argument("--symbol", default="NVDA.US")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--interval", choices=["1d", "1m"], default="1m")
    p.add_argument("--capital", type=float, default=20000.0)
    p.add_argument("--rate", type=float, default=0.0003)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--size", type=int, default=1)
    p.add_argument("--pricetick", type=float, default=0.01)

    # Single-backtest output
    p.add_argument(
        "--output",
        default="",
        help="Single-backtest output JSON path (default state/runs/classic_multifactor/vnpy_cta_backtest_report.json)",
    )

    # Optimization switches
    p.add_argument("--optimize", choices=["bf", "ga"], default="", help="Enable bf/ga sweep mode")
    p.add_argument("--target", default=DEFAULT_TARGET, help=f"Optimization target (default {DEFAULT_TARGET})")
    p.add_argument(
        "--opt-param",
        action="append",
        default=[],
        help="Sweep spec, repeatable: KEY=START:END:STEP (variable) or KEY=VALUE (fixed)",
    )
    p.add_argument("--opt-workers", type=int, default=None, help="Max parallel workers (default: all CPU cores)")
    p.add_argument(
        "--opt-output",
        default="",
        help=f"Sweep report path (default {DEFAULT_SWEEP_DIR}/cta_sweep_<symbol>_<ts>.json)",
    )
    p.add_argument("--opt-top-n", type=int, default=20, help="Keep top N sweep results in the report (default 20)")
    p.add_argument("--opt-pop-size", type=int, default=100, help="[ga only] population size")
    p.add_argument("--opt-ngen", type=int, default=30, help="[ga only] number of generations")
    p.add_argument("--opt-cxpb", type=float, default=0.95, help="[ga only] crossover probability")
    return p


def _merge_setting_from_cli_and_config(
    config: dict[str, Any],
) -> dict[str, Any]:
    """Extract the strategy setting from a JSON config; empty dict if absent."""
    setting = config.get("setting") or {}
    if not isinstance(setting, dict):
        raise ValueError(f"config.setting must be a dict, got {type(setting).__name__}")
    return dict(setting)


def _resolve_runtime(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """Combine CLI + JSON config into runtime kwargs. CLI wins on conflicts
    only when the user changed the CLI default; otherwise config wins.

    Since argparse can't easily tell defaults from explicit values without
    sentinels, we give precedence to JSON config for symbol/interval/capital/
    dates when present.
    """
    from scripts.classic_multifactor.data import parse_symbol

    symbol = config.get("symbol") or args.symbol
    interval = config.get("interval") or args.interval

    # Dates: CLI wins when config has none; otherwise fallback chain.
    start_str = args.start
    end_str = args.end
    backtest_cfg = config.get("backtest") if isinstance(config.get("backtest"), dict) else {}
    if backtest_cfg.get("start"):
        start_str = backtest_cfg["start"]
    if backtest_cfg.get("end"):
        end_str = backtest_cfg["end"]

    capital = float(config.get("_meta", {}).get("capital") or config.get("capital") or args.capital)

    market, _input_form, vt_symbol, _futu_code = parse_symbol(symbol)

    return {
        "symbol": symbol,
        "market": market,
        "vt_symbol": vt_symbol,
        "interval": interval,
        "start": datetime.fromisoformat(start_str),
        "end": datetime.fromisoformat(end_str),
        "capital": capital,
    }


def _run_single(args: argparse.Namespace, config: dict[str, Any]) -> None:
    from scripts.classic_multifactor.data import VnpyBarRepository

    rt = _resolve_runtime(args, config)
    setting = _merge_setting_from_cli_and_config(config)
    # Ensure capital is consistent in the strategy setting.
    setting.setdefault("capital", rt["capital"])

    VnpyBarRepository(fetch_futu_history=True).load_bars(
        rt["symbol"], rt["start"], rt["end"], rt["interval"]
    )

    stats, _engine = ClassicCtaBacktestRunner().run(
        vt_symbol=rt["vt_symbol"],
        interval=rt["interval"],
        start=rt["start"],
        end=rt["end"],
        capital=rt["capital"],
        rate=args.rate,
        slippage=args.slippage,
        size=args.size,
        pricetick=args.pricetick,
        setting=setting,
    )
    report = {
        "strategy": "classic_multifactor_no_llm_vnpy_cta",
        "symbol": rt["symbol"],
        "vt_symbol": rt["vt_symbol"],
        "interval": rt["interval"],
        "start": rt["start"].isoformat(),
        "end": rt["end"].isoformat(),
        "setting": setting,
        "stats": stats,
    }
    out = Path(args.output) if args.output else REPO_ROOT / "state" / "runs" / "classic_multifactor" / "vnpy_cta_backtest_report.json"
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(out)
    print(json.dumps(report["stats"], ensure_ascii=False, indent=2, default=str))


def _run_sweep(args: argparse.Namespace, config: dict[str, Any]) -> None:
    from scripts.classic_multifactor.data import VnpyBarRepository

    rt = _resolve_runtime(args, config)
    base_setting = _merge_setting_from_cli_and_config(config)
    base_setting.setdefault("capital", rt["capital"])

    if not args.opt_param:
        raise SystemExit("--optimize requires at least one --opt-param (KEY=START:END:STEP)")

    opt_setting = build_opt_setting(args.target, args.opt_param)
    sweep_space = {k: list(v) for k, v in opt_setting.params.items()}

    # Preload bars once; vnpy sub-processes will reload via database.
    VnpyBarRepository(fetch_futu_history=True).load_bars(
        rt["symbol"], rt["start"], rt["end"], rt["interval"]
    )

    ga_kwargs: dict[str, Any] = {}
    if args.optimize == "ga":
        ga_kwargs = {
            "pop_size": int(args.opt_pop_size),
            "ngen": int(args.opt_ngen),
            "cxpb": float(args.opt_cxpb),
        }

    print(
        f"[sweep] mode={args.optimize} target={args.target} "
        f"space_size={sum(len(v) for v in sweep_space.values())} "
        f"combinations={_count_combinations(sweep_space)}"
    )

    results = ClassicCtaBacktestRunner().run_optimization(
        vt_symbol=rt["vt_symbol"],
        interval=rt["interval"],
        start=rt["start"],
        end=rt["end"],
        capital=rt["capital"],
        rate=args.rate,
        slippage=args.slippage,
        size=args.size,
        pricetick=args.pricetick,
        base_setting=base_setting,
        opt_setting=opt_setting,
        mode=args.optimize,
        max_workers=args.opt_workers,
        ga_kwargs=ga_kwargs,
    )

    if args.opt_output:
        out = Path(args.opt_output)
        if not out.is_absolute():
            out = REPO_ROOT / out
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_symbol = rt["symbol"].replace(".", "_").replace("/", "_")
        out = DEFAULT_SWEEP_DIR / f"cta_sweep_{safe_symbol}_{args.optimize}_{ts}.json"

    written = dump_sweep_results(
        results=results,
        output_path=out,
        mode=args.optimize,
        target=args.target,
        vt_symbol=rt["vt_symbol"],
        interval=rt["interval"],
        start=rt["start"],
        end=rt["end"],
        base_setting=base_setting,
        sweep_space=sweep_space,
        top_n=int(args.opt_top_n),
    )
    print(f"[sweep] wrote {written}")
    if results:
        top = results[0]
        print(f"[sweep] top#1 params={top[0]} target={top[1]}")


def _count_combinations(space: dict[str, list]) -> int:
    total = 1
    for values in space.values():
        total *= max(1, len(values))
    return total


def main() -> None:
    args = _build_argparser().parse_args()
    config = load_config(args.config) if args.config else {}
    if args.optimize:
        _run_sweep(args, config)
    else:
        _run_single(args, config)


if __name__ == "__main__":
    main()
