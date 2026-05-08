from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.classic_multifactor.flow import UsSingleSymbolClassicFlow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="无大模型经典多因子：美股单标的回测 / Futu SIM 单轮交易")
    parser.add_argument("--mode", choices=["backtest", "sim-once"], default="backtest")
    parser.add_argument("--symbol", default="NVDA.US")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--capital", type=float, default=20000.0)
    parser.add_argument("--interval", choices=["1d", "1m"], default="1d")
    parser.add_argument("--max-order-value", type=float, default=5000.0)

    parser.add_argument("--max-position-pct", type=float, default=0.35)
    parser.add_argument("--fast-window", type=int, default=10)
    parser.add_argument("--slow-window", type=int, default=60)
    parser.add_argument("--momentum-window", type=int, default=20)
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--entry-score", type=float, default=0.62)
    parser.add_argument("--exit-score", type=float, default=0.46)
    parser.add_argument("--stop-loss-pct", type=float, default=0.08)
    parser.add_argument("--take-profit-pct", type=float, default=0.22)
    parser.add_argument("--trailing-stop-pct", type=float, default=0.12)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--signal-interval-minutes", type=int, default=1)
    parser.add_argument("--confirm-bars", type=int, default=1)
    parser.add_argument("--min-volume-ratio", type=float, default=0.0)
    parser.add_argument("--min-atr-pct", type=float, default=0.0)
    parser.add_argument("--min-trend-score", type=float, default=0.60)

    parser.add_argument("--stop-atr", type=float, default=0.0)
    parser.add_argument("--take-profit-atr", type=float, default=0.0)
    parser.add_argument("--trailing-atr", type=float, default=0.0)
    parser.add_argument("--max-intraday-trades", type=int, default=0)
    parser.add_argument("--entry-cooldown-minutes", type=int, default=0)
    parser.add_argument("--min-hold-minutes", type=int, default=0)
    parser.add_argument("--no-new-entry-after", default="")
    parser.add_argument("--fetch-futu-history", action="store_true", default=True)

    parser.add_argument("--no-fetch-futu-history", dest="fetch_futu_history", action="store_false")
    parser.add_argument("--submit-sim", action="store_true", help="sim-once 模式下真实提交到 Futu 模拟盘；默认只生成决策")
    parser.add_argument("--output", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    UsSingleSymbolClassicFlow(args).run()


if __name__ == "__main__":
    main()
