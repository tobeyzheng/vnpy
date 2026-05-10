"""HK live trading task entry.

Top-level wrapper that forwards into
``scripts/classic_multifactor/run_intraday_loop.py`` for Hong Kong market
live-task orchestration.

This script itself does not connect to OpenD or submit orders. It only
validates the target config path, preserves ``--live-submit`` as an explicit
intent flag, and forwards into the real runner with HK-safe defaults unless
the caller already overrides them.

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


DEFAULT_CLASSIC_CONFIG = REPO_ROOT / "configs" / "classic_multifactor" / "tencent_hk_g01.json"
DEFAULT_REPORT_FILENAME = "hk_live_task_report.json"
DEFAULT_SESSION_TZ = "Asia/Hong_Kong"
DEFAULT_FUTU_ENV = "真实"
DEFAULT_FUTU_MARKET = "HK"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "HK live trading task wrapper for the vnpy-native intraday mainline. "
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
            "Defaults to configs/classic_multifactor/tencent_hk_g01.json."
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
            "(e.g. --session-start, --session-end, --state-root). Use as the LAST argument."
        ),
    )
    return parser


def _inject_default_extra(extra: list[str], *, flag: str, value: str) -> list[str]:
    if flag in extra:
        return extra
    return [*extra, flag, value]


def _run_vnpy_mainline(classic_config: str, live_submit: bool, extra: list[str]) -> int:
    cfg_path = Path(classic_config).expanduser().resolve()
    if not cfg_path.is_file():
        print(
            f"[run_hk_live_task] classic config not found: {cfg_path}",
            file=sys.stderr,
        )
        return 2
    runner = REPO_ROOT / "scripts" / "classic_multifactor" / "run_intraday_loop.py"
    if not runner.is_file():
        print(
            f"[run_hk_live_task] runner missing: {runner}",
            file=sys.stderr,
        )
        return 2
    if extra and extra[0] == "--":
        extra = extra[1:]
    extra = _inject_default_extra(list(extra), flag="--session-tz", value=DEFAULT_SESSION_TZ)
    extra = _inject_default_extra(extra, flag="--report-filename", value=DEFAULT_REPORT_FILENAME)
    extra = _inject_default_extra(extra, flag="--futu-env", value=DEFAULT_FUTU_ENV)
    extra = _inject_default_extra(extra, flag="--futu-market", value=DEFAULT_FUTU_MARKET)
    cmd = [sys.executable, str(runner), "--config", str(cfg_path)]
    if live_submit:
        cmd.append("--live-submit")
    cmd.extend(extra)
    print(
        f"[run_hk_live_task] vnpy-mainline forward: {' '.join(cmd)}",
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
