"""HK Futu SIM session entry.

Top-level wrapper around ``scripts/classic_multifactor/run_intraday_loop.py``
for Hong Kong market paper-session observation.

The wrapper itself does not connect to OpenD or submit orders. It only
forwards into the vnpy-native intraday runner with HK-safe defaults unless
the caller overrides them in ``--classic-extra``.
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
DEFAULT_REPORT_FILENAME = "hk_futu_sim_session_report.json"
DEFAULT_SESSION_TZ = "Asia/Hong_Kong"
DEFAULT_FUTU_ENV = "模拟"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "HK Futu SIM session wrapper for the vnpy-native intraday mainline. "
            "Defaults to Hong Kong session timezone, report filename, and SIM environment."
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
            "(e.g. --session-start, --session-end, --max-bars). Use as the LAST argument."
        ),
    )
    return parser


def _inject_default_extra(extra: list[str], *, flag: str, value: str) -> list[str]:
    if flag in extra:
        return extra
    return [*extra, flag, value]


def _run_vnpy_mainline(classic_config: str, extra: list[str]) -> int:
    cfg_path = Path(classic_config).expanduser().resolve()
    if not cfg_path.is_file():
        print(
            f"[run_hk_futu_sim_session] classic config not found: {cfg_path}",
            file=sys.stderr,
        )
        return 2
    runner = REPO_ROOT / "scripts" / "classic_multifactor" / "run_intraday_loop.py"
    if not runner.is_file():
        print(
            f"[run_hk_futu_sim_session] runner missing: {runner}",
            file=sys.stderr,
        )
        return 2
    if extra and extra[0] == "--":
        extra = extra[1:]
    extra = _inject_default_extra(list(extra), flag="--session-tz", value=DEFAULT_SESSION_TZ)
    extra = _inject_default_extra(extra, flag="--report-filename", value=DEFAULT_REPORT_FILENAME)
    extra = _inject_default_extra(extra, flag="--futu-env", value=DEFAULT_FUTU_ENV)
    cmd = [sys.executable, str(runner), "--config", str(cfg_path), *extra]
    print(
        f"[run_hk_futu_sim_session] vnpy-mainline forward: {' '.join(cmd)}",
        flush=True,
    )
    os.execv(cmd[0], cmd)
    return 0


def main() -> None:
    args = build_parser().parse_args()
    rc = _run_vnpy_mainline(args.classic_config, list(args.classic_extra or []))
    sys.exit(rc)


if __name__ == "__main__":
    main()
