"""US live trading task entry.

Top-level wrapper that forwards into
``scripts/classic_multifactor/run_intraday_loop.py`` (the vnpy-native
minute-level mainline with ``MainEngine + FutuGateway + CtaEngine`` and the
shared four-stage ``ExecutionGuardPipeline``).

This script itself does not connect to OpenD or submit orders. It only
validates the target config path, preserves ``--live-submit`` as an explicit
intent flag, and then ``exec``-forwards into the real runner.

Real order submission still requires all hard switches in the downstream
runner: ``--live-submit`` + ``VNPY_LIVE_CONFIG=YES`` +
``VNPY_LIVE_SUBMIT=YES`` + ``VNPY_LIVE_APPROVED=YES``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_CLASSIC_CONFIG = REPO_ROOT / "configs" / "classic_multifactor" / "nvda_g09.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "US live trading task wrapper for the vnpy-native intraday mainline. "
            "This script only forwards to scripts/classic_multifactor/run_intraday_loop.py. "
            "Real submission still requires --live-submit plus the VNPY_LIVE_* hard switches."
        ),
    )
    parser.add_argument(
        "--classic-config",
        type=str,
        default=str(DEFAULT_CLASSIC_CONFIG),
        help=(
            "Path to a classic-multifactor intraday config JSON. "
            "Defaults to configs/classic_multifactor/nvda_g09.json."
        ),
    )
    parser.add_argument(
        "--live-submit",
        action="store_true",
        help="Forward the explicit live-submit intent flag to the intraday runner.",
    )
    parser.add_argument(
        "--classic-extra",
        nargs=argparse.REMAINDER,
        default=[],
        help=(
            "Pass-through extra args appended to run_intraday_loop.py "
            "(e.g. --session-start, --session-end, --futu-env, --state-root). "
            "Use as the LAST argument."
        ),
    )
    return parser


def _run_vnpy_mainline(classic_config: str, live_submit: bool, extra: list[str]) -> int:
    cfg_path = Path(classic_config).expanduser().resolve()
    if not cfg_path.is_file():
        print(
            f"[run_us_live_task] classic config not found: {cfg_path}",
            file=sys.stderr,
        )
        return 2
    runner = REPO_ROOT / "scripts" / "classic_multifactor" / "run_intraday_loop.py"
    if not runner.is_file():
        print(
            f"[run_us_live_task] runner missing: {runner}",
            file=sys.stderr,
        )
        return 2
    if extra and extra[0] == "--":
        extra = extra[1:]
    cmd = [sys.executable, str(runner), "--config", str(cfg_path)]
    if live_submit:
        cmd.append("--live-submit")
    cmd.extend(extra)
    print(
        f"[run_us_live_task] vnpy-mainline forward: {' '.join(cmd)}",
        flush=True,
    )
    os.execv(cmd[0], cmd)
    return 0


def main() -> None:
    args = build_parser().parse_args()
    rc = _run_vnpy_mainline(
        classic_config=args.classic_config,
        live_submit=args.live_submit,
        extra=list(args.classic_extra or []),
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
