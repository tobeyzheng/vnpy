"""6-switch safety validator for phase2 live trading.

Maps the requirements doc §6 verbatim:

| execution_env | required CLI flags        | required env vars                                                                              |
| ------------- | ------------------------- | ---------------------------------------------------------------------------------------------- |
| dry_run       | (none)                    | (none)                                                                                         |
| futu_sim      | --live-submit             | FUTU_HOST, FUTU_PORT, FUTU_MARKET=US                                                           |
| futu_real     | --live-submit             | FUTU_HOST, FUTU_PORT, FUTU_MARKET=US, VNPY_LIVE_CONFIG=YES, VNPY_LIVE_SUBMIT=YES,               |
|               |                           | VNPY_LIVE_APPROVED=YES, FUTU_ENV=真实, FUTU_TRADE_PASSWORD non-empty                            |

The validator is a *pure* function: it takes the parsed CLI flags + the
``os.environ`` mapping and returns a structured ``SafetyDecision`` that the
runner can either honour or print-and-exit on. The validator MUST NOT mutate
the environment, MUST NOT touch OpenD, and MUST NOT log secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

__all__ = [
    "SafetyDecision",
    "validate_safety",
    "ExecutionEnvLiteral",
    "REAL_REQUIRED_ENV_FLAGS",
    "SIM_REQUIRED_ENV_KEYS",
]


# Three-state execution environment string used everywhere in phase2 live.
ExecutionEnvLiteral = str  # one of {"dry_run", "futu_sim", "futu_real"}


# Environment keys whose *value* must equal a specific literal in REAL mode.
REAL_REQUIRED_ENV_FLAGS: dict[str, str] = {
    "VNPY_LIVE_CONFIG": "YES",
    "VNPY_LIVE_SUBMIT": "YES",
    "VNPY_LIVE_APPROVED": "YES",
    "FUTU_ENV": "真实",
}

# OpenD connectivity envs required by both SIM and REAL.
SIM_REQUIRED_ENV_KEYS: tuple[str, ...] = ("FUTU_HOST", "FUTU_PORT", "FUTU_MARKET")


@dataclass(frozen=True)
class SafetyDecision:
    """Outcome of the 6-switch validator.

    - ``allowed``     : whether the runner is allowed to enter the main loop.
    - ``execution_env`` : resolved {dry_run, futu_sim, futu_real} string.
    - ``missing``     : ordered list of switches that failed; empty when allowed.
    - ``checked``     : ordered list of switches that were checked. Useful for
                        the daily report so audits can see what gates were in
                        play even on a green run.
    - ``warnings``    : non-fatal hints (e.g. "FUTU_TRADE_PASSWORD set in SIM
                        mode is unused"). Never blocks execution.

    None of the fields contains the actual env *values* — only switch names —
    so this object is safe to dump into ``daily_report.json``.
    """

    allowed: bool
    execution_env: ExecutionEnvLiteral
    missing: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def explain(self) -> str:
        if self.allowed:
            return (
                f"safety: allowed (execution_env={self.execution_env}, "
                f"checks_passed={len(self.checked)})"
            )
        bullet = "\n  - "
        return (
            f"safety: BLOCKED (execution_env={self.execution_env}); "
            f"missing switches:{bullet}{bullet.join(self.missing)}"
        )


def _resolve_execution_env(*, futu_env_cli: str | None, live_submit: bool) -> ExecutionEnvLiteral:
    """Map (--futu-env, --live-submit) to the canonical execution_env string.

    - --futu-env in {None, ""} or --live-submit False → dry_run
    - --futu-env "模拟"/"sim"/"simulate" + live_submit → futu_sim
    - --futu-env "真实"/"real"           + live_submit → futu_real

    Unknown --futu-env values fall back to dry_run so the safety check below
    will reject the run with a clear "missing" report rather than silently
    promoting it to REAL.
    """
    if not live_submit:
        return "dry_run"
    if not futu_env_cli:
        return "dry_run"
    s = futu_env_cli.strip().lower()
    if s in {"模拟", "sim", "simulate", "simulated"}:
        return "futu_sim"
    if s in {"真实", "real"}:
        return "futu_real"
    # Original Chinese-character branch (case is irrelevant for CJK).
    if futu_env_cli.strip() == "模拟":
        return "futu_sim"
    if futu_env_cli.strip() == "真实":
        return "futu_real"
    return "dry_run"


def validate_safety(
    *,
    futu_env_cli: str | None,
    live_submit: bool,
    env: Mapping[str, str] | None = None,
) -> SafetyDecision:
    """Run the 6-switch validator.

    Parameters
    ----------
    futu_env_cli : value of the ``--futu-env`` CLI flag (None when omitted).
    live_submit  : value of the ``--live-submit`` CLI flag.
    env          : env mapping to inspect. Defaults to ``os.environ``. Pass an
                   explicit dict in tests to keep them hermetic.

    Returns
    -------
    SafetyDecision with ``allowed=True`` only when every required switch for
    the resolved ``execution_env`` is in place.
    """
    env_map = dict(env) if env is not None else dict(os.environ)
    execution_env = _resolve_execution_env(
        futu_env_cli=futu_env_cli, live_submit=live_submit
    )

    missing: list[str] = []
    checked: list[str] = []
    warnings: list[str] = []

    if execution_env == "dry_run":
        # No safety prerequisites: the runner stays in dry mode and never
        # touches OpenD. We still surface a warning if the user *thought* they
        # were going live but left a switch off.
        if futu_env_cli and not live_submit:
            warnings.append(
                "futu_env requested but --live-submit not set; staying in dry_run"
            )
        return SafetyDecision(
            allowed=True,
            execution_env=execution_env,
            missing=missing,
            checked=checked,
            warnings=warnings,
        )

    # --- both SIM and REAL require --live-submit + OpenD connectivity ------
    checked.append("--live-submit")
    if not live_submit:
        missing.append("--live-submit")

    for key in SIM_REQUIRED_ENV_KEYS:
        checked.append(f"env:{key}")
        if not env_map.get(key):
            missing.append(f"env:{key}")

    market = env_map.get("FUTU_MARKET", "")
    if market and market.upper() != "US":
        missing.append(f"env:FUTU_MARKET=US(got={market})")

    if execution_env == "futu_real":
        # 4 phase2-flagged env vars + non-empty trade password.
        for key, expected in REAL_REQUIRED_ENV_FLAGS.items():
            checked.append(f"env:{key}={expected}")
            if env_map.get(key) != expected:
                missing.append(f"env:{key}={expected}")
        checked.append("env:FUTU_TRADE_PASSWORD")
        if not env_map.get("FUTU_TRADE_PASSWORD"):
            missing.append("env:FUTU_TRADE_PASSWORD")
    else:
        # SIM should NOT require any VNPY_LIVE_* / FUTU_TRADE_PASSWORD; warn
        # if they are accidentally set (not blocking).
        for key in REAL_REQUIRED_ENV_FLAGS:
            if env_map.get(key):
                warnings.append(
                    f"env:{key} set but execution_env=futu_sim; ignored"
                )
        if env_map.get("FUTU_TRADE_PASSWORD"):
            warnings.append(
                "env:FUTU_TRADE_PASSWORD set but execution_env=futu_sim; ignored"
            )

    return SafetyDecision(
        allowed=not missing,
        execution_env=execution_env,
        missing=missing,
        checked=checked,
        warnings=warnings,
    )
