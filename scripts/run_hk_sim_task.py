"""HK SIM trading task entry.

Legacy default: dispatches to ``services.trading_pipeline.sim_task`` (the
candidate-pool + StrategyEngine + SimAccountStore stack).

New mainline: pass ``--use-vnpy-mainline`` to forward into
``scripts/classic_multifactor/run_intraday_loop.py``. The wrapper injects
HK-safe defaults for ``--session-tz`` and ``--report-filename`` unless the
caller already provided them via ``--classic-extra``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.trading_pipeline import MarketSimTaskConfig, MultiMarketSimTradingPipeline


DEFAULT_CLASSIC_CONFIG = REPO_ROOT / "configs" / "classic_multifactor" / "tencent_hk_g01.json"
DEFAULT_REPORT_FILENAME = "hk_sim_task_report.json"
DEFAULT_SESSION_TZ = "Asia/Hong_Kong"
DEFAULT_FUTU_MARKET = "HK"

CONFIG = MarketSimTaskConfig(
    market="hong_kong",
    task_name="hk_real_env_sim_trading_v1",
    account_filename="hk_sim_account.json",
    report_filename=DEFAULT_REPORT_FILENAME,
    budget_per_trade=30000.0,
    lot_size_default=100,
    quote_prefix="HK",
    symbol_suffix="HK",
    flow_divisor=3e9,
    catalyst_keywords=("回购", "南下", "AI", "催化"),
    affordability_label="one-lot",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HK SIM trading task (legacy + vnpy-mainline switch)",
    )
    parser.add_argument(
        "--use-vnpy-mainline",
        action="store_true",
        help=(
            "Forward to scripts/classic_multifactor/run_intraday_loop.py "
            "(new ClassicMultiFactorCtaStrategy + ExecutionGuardPipeline "
            "main-line). Uses HK-safe defaults for session timezone and "
            "report filename unless explicitly overridden."
        ),
    )
    parser.add_argument(
        "--classic-config",
        type=str,
        default=str(DEFAULT_CLASSIC_CONFIG),
        help=(
            "Path to a classic-multifactor intraday config JSON. "
            "Defaults to configs/classic_multifactor/tencent_hk_g01.json."
        ),
    )
    parser.add_argument(
        "--classic-extra",
        nargs=argparse.REMAINDER,
        default=[],
        help=(
            "Pass-through extra args appended to run_intraday_loop.py "
            "(e.g. --max-bars, --session-start). Use as the LAST argument."
        ),
    )
    return parser


def _run_legacy() -> None:
    pipeline = MultiMarketSimTradingPipeline(REPO_ROOT, CONFIG)
    report = pipeline.run()
    print(pipeline.report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _inject_default_extra(extra: list[str], *, flag: str, value: str) -> list[str]:
    if flag in extra:
        return extra
    return [*extra, flag, value]


def _run_vnpy_mainline(classic_config: str, extra: list[str]) -> int:
    cfg_path = Path(classic_config).expanduser().resolve()
    if not cfg_path.is_file():
        print(
            f"[run_hk_sim_task] classic config not found: {cfg_path}",
            file=sys.stderr,
        )
        return 2
    runner = REPO_ROOT / "scripts" / "classic_multifactor" / "run_intraday_loop.py"
    if not runner.is_file():
        print(
            f"[run_hk_sim_task] runner missing: {runner}",
            file=sys.stderr,
        )
        return 2
    if extra and extra[0] == "--":
        extra = extra[1:]
    extra = _inject_default_extra(list(extra), flag="--session-tz", value=DEFAULT_SESSION_TZ)
    extra = _inject_default_extra(extra, flag="--report-filename", value=DEFAULT_REPORT_FILENAME)
    extra = _inject_default_extra(extra, flag="--futu-market", value=DEFAULT_FUTU_MARKET)
    cmd = [sys.executable, str(runner), "--config", str(cfg_path), *extra]
    print(
        f"[run_hk_sim_task] vnpy-mainline forward: {' '.join(cmd)}",
        flush=True,
    )
    os.execv(cmd[0], cmd)
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    if args.use_vnpy_mainline:
        rc = _run_vnpy_mainline(args.classic_config, list(args.classic_extra or []))
        sys.exit(rc)
    _run_legacy()


if __name__ == "__main__":
    main()
