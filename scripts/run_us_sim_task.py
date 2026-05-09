"""US SIM trading task entry.

Legacy default: dispatches to ``services.trading_pipeline.sim_task`` (the
candidate-pool + StrategyEngine + SimAccountStore stack).

New mainline (R1b, Task 8): pass ``--use-vnpy-mainline`` to forward into
``scripts/classic_multifactor/run_intraday_loop.py``, which uses vnpy's
``MainEngine`` + ``ClassicMultiFactorCtaStrategy`` + four-stage
``ExecutionGuardPipeline``. The legacy path is preserved for fallback.

See project rule 3 and ``docs/system_integration_guide.md`` for the
boundary.
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


CONFIG = MarketSimTaskConfig(
    market="us",
    task_name="us_real_env_sim_trading_v1",
    account_filename="us_sim_account.json",
    report_filename="us_sim_task_report.json",
    budget_per_trade=5000.0,
    lot_size_default=1,
    quote_prefix="US",
    symbol_suffix="US",
    flow_divisor=5e9,
    catalyst_keywords=("AI", "催化"),
    affordability_label="one-unit",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="US SIM trading task (legacy + vnpy-mainline switch)",
    )
    parser.add_argument(
        "--use-vnpy-mainline",
        action="store_true",
        help=(
            "Forward to scripts/classic_multifactor/run_intraday_loop.py "
            "(new ClassicMultiFactorCtaStrategy + ExecutionGuardPipeline "
            "main-line). Requires --classic-config to point at an intraday "
            "config JSON. See R1b / Task 8."
        ),
    )
    parser.add_argument(
        "--classic-config",
        type=str,
        default=None,
        help=(
            "Path to a classic-multifactor intraday config JSON; required "
            "when --use-vnpy-mainline is set."
        ),
    )
    parser.add_argument(
        "--classic-extra",
        nargs=argparse.REMAINDER,
        default=[],
        help=(
            "Pass-through extra args appended to run_intraday_loop.py "
            "(e.g. --max-bars, --session-tz). Use as the LAST argument."
        ),
    )
    return parser


def _run_legacy() -> None:
    repo = Path(__file__).resolve().parents[1]
    pipeline = MultiMarketSimTradingPipeline(repo, CONFIG)
    report = pipeline.run()
    print(pipeline.report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _run_vnpy_mainline(classic_config: str | None, extra: list[str]) -> int:
    if not classic_config:
        print(
            "[run_us_sim_task] --use-vnpy-mainline requires --classic-config "
            "<path-to-intraday-json>",
            file=sys.stderr,
        )
        return 2
    cfg_path = Path(classic_config).expanduser().resolve()
    if not cfg_path.is_file():
        print(
            f"[run_us_sim_task] classic config not found: {cfg_path}",
            file=sys.stderr,
        )
        return 2
    runner = REPO_ROOT / "scripts" / "classic_multifactor" / "run_intraday_loop.py"
    if not runner.is_file():
        print(
            f"[run_us_sim_task] runner missing: {runner}",
            file=sys.stderr,
        )
        return 2
    # Strip a leading literal "--" that argparse.REMAINDER may keep.
    if extra and extra[0] == "--":
        extra = extra[1:]
    cmd = [sys.executable, str(runner), "--config", str(cfg_path), *extra]
    print(
        f"[run_us_sim_task] vnpy-mainline forward: {' '.join(cmd)}",
        flush=True,
    )
    os.execv(cmd[0], cmd)
    return 0  # unreachable


def main() -> None:
    args = _build_parser().parse_args()
    if args.use_vnpy_mainline:
        rc = _run_vnpy_mainline(args.classic_config, list(args.classic_extra or []))
        sys.exit(rc)
    _run_legacy()


if __name__ == "__main__":
    main()
