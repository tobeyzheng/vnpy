from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.classic_multifactor.data import VnpyBarRepository, parse_us_symbol
from scripts.classic_multifactor.external import JsonlSelectionReplayProvider
from vnpy.trader.constant import Interval



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classic multifactor AlphaStrategy backtest skeleton")
    parser.add_argument("--symbols", nargs="+", default=["NVDA.US", "MSFT.US"])
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--capital", type=float, default=50000.0)
    parser.add_argument("--interval", choices=["1d", "1m"], default="1d")
    parser.add_argument("--external-selection-root", default="")

    parser.add_argument("--output", default="")
    return parser


def write_report(report: dict, output: str = "") -> None:
    out = Path(output) if output else REPO_ROOT / "state" / "runs" / "classic_multifactor" / "alpha_backtest_report.json"
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(out)
    print(json.dumps(report.get("stats", report), ensure_ascii=False, indent=2, default=str))


def main() -> None:
    args = build_parser().parse_args()
    try:
        import polars as pl  # type: ignore
        from scripts.classic_multifactor.alpha_strategy import ClassicMultiFactorAlphaStrategy
        from vnpy.alpha import AlphaLab
        from vnpy.alpha.strategy.backtesting import BacktestingEngine
    except ModuleNotFoundError as exc:
        write_report(
            {
                "strategy": "classic_multifactor_alpha_with_external_overlay",
                "status": "dependency_missing",
                "message": f"AlphaStrategy backtest requires optional dependency: {exc.name}",
                "next_step": "install vnpy alpha optional dependencies or use cta-backtest mode first",
                "symbols": args.symbols,
            },
            args.output,
        )
        return
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    lab = AlphaLab(str(REPO_ROOT / "state" / "runs" / "classic_multifactor" / "alpha_lab"))

    repo = VnpyBarRepository(fetch_futu_history=True)
    vt_symbols: list[str] = []
    for symbol in args.symbols:
        _us_symbol, vt_symbol, _futu_code = parse_us_symbol(symbol)
        _loaded_vt, _futu, bars = repo.load_us_bars(symbol, start, end, args.interval)

        if bars:
            lab.save_bar_data(bars)
            lab.add_contract_setting(vt_symbol, long_rate=0.0003, short_rate=0.0003, size=1, pricetick=0.01)
            vt_symbols.append(vt_symbol)
    engine = BacktestingEngine(lab)
    engine.set_parameters(vt_symbols=vt_symbols, interval=Interval.MINUTE if args.interval == "1m" else Interval.DAILY, start=start, end=end, capital=int(args.capital), risk_free=0.0, annual_days=252)

    setting = {}
    if args.external_selection_root:
        setting["external_provider"] = JsonlSelectionReplayProvider(args.external_selection_root)
    signal_df = pl.DataFrame({"datetime": []})
    engine.add_strategy(ClassicMultiFactorAlphaStrategy, setting, signal_df)
    engine.load_data()
    engine.run_backtesting()
    result = engine.calculate_result()
    stats = engine.calculate_statistics() if result is not None else {"status": "no_data", "message": "alpha backtest produced no trades"}
    report = {"strategy": "classic_multifactor_alpha_with_external_overlay", "status": "ok", "interval": args.interval, "symbols": args.symbols, "vt_symbols": vt_symbols, "stats": stats}

    write_report(report, args.output)



if __name__ == "__main__":
    main()
