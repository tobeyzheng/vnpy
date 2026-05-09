from __future__ import annotations

"""Dual-run preflight self-check (zero-side-effect).

This script is the automated checklist that must pass before launching
a SIM dual-run trading day (see §18 of docs/system_integration_guide.md).
It is **strictly read-only**:

* It never connects to Futu OpenD.
* It never submits any order or touches the brokerage.
* It never modifies ``state/runs/`` or any other persisted file.
* It only invokes ``git`` / ``python3`` / filesystem ``stat`` / ``shutil.disk_usage``.

If any required check fails, the script exits with code 1 and prints
a checklist summary to stdout. A JSON report is also written under
``state/runs/reports/preflight_<YYYYMMDD>.json`` (in the *current* working
directory, **not** in either of the dual-run worktrees) so the audit log
stays out of the worktrees being checked.

Usage
-----
::

    python3 scripts/dual_run_preflight.py \\
        --run-a /data/dual_run/legacy \\
        --run-b /data/dual_run/vnpy_native \\
        --expected-tag-a classic-pre-vnpy-rewrite-v1 \\
        --expected-branch-b classic-vnpy-native-rewrite \\
        --config configs/classic_multifactor/nvda_g09.json

The ``--config`` path is resolved **inside each worktree** (so each
worktree must have an identical file at the same relative path). The
script computes SHA256 over both copies and reports any drift.

Exit codes
----------
* ``0`` — all checks pass (or only soft warnings).
* ``1`` — at least one hard check failed.
* ``2`` — invocation error (missing CLI args, unreadable worktree).
"""

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Statuses considered "still in flight" — same as diff_dual_run.py.
_OPEN_STATUS = {
    "created", "validated", "risk_checked", "approval_required", "approved",
    "submitting", "submitted", "partial_filled", "cancel_requested",
}

_DEFAULT_MIN_DISK_GB = 1.0


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorktreeReport:
    label: str
    path: Path
    exists: bool = False
    is_git: bool = False
    head_sha: str | None = None
    branch_name: str | None = None
    head_tags: list[str] = field(default_factory=list)
    is_dirty: bool = False
    dirty_files: list[str] = field(default_factory=list)
    config_path: Path | None = None
    config_present: bool = False
    config_sha256: str | None = None
    orders_residual: int = 0
    orders_residual_sample: list[str] = field(default_factory=list)
    python_version: str | None = None
    disk_free_gb: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a command capturing stdout/stderr; never raises."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"


def _sha256_of_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fp:
            for chunk in iter(lambda: fp.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Worktree probe
# ---------------------------------------------------------------------------

def probe_worktree(label: str, root: Path, config_relpath: str | None) -> WorktreeReport:
    """Collect read-only facts about a single worktree.

    Never raises — every failure is recorded into the WorktreeReport
    fields so the caller can render a structured checklist.
    """

    rep = WorktreeReport(label=label, path=root)
    if not root.exists() or not root.is_dir():
        return rep
    rep.exists = True

    if not (root / ".git").exists():
        return rep
    rep.is_git = True

    rc, sha, _ = _run(["git", "rev-parse", "HEAD"], root)
    if rc == 0 and sha:
        rep.head_sha = sha

    rc, branch, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    if rc == 0 and branch:
        rep.branch_name = branch

    rc, tags, _ = _run(["git", "tag", "--points-at", "HEAD"], root)
    if rc == 0 and tags:
        rep.head_tags = [t.strip() for t in tags.splitlines() if t.strip()]

    rc, dirty, _ = _run(["git", "status", "--porcelain"], root)
    if rc == 0:
        lines = [line for line in dirty.splitlines() if line.strip()]
        rep.is_dirty = bool(lines)
        rep.dirty_files = lines[:20]

    if config_relpath:
        cfg = (root / config_relpath).resolve()
        rep.config_path = cfg
        if cfg.exists() and cfg.is_file():
            rep.config_present = True
            rep.config_sha256 = _sha256_of_file(cfg)

    orders_dir = root / "state" / "runs" / "orders"
    if orders_dir.exists():
        for path in orders_dir.glob("*.json"):
            data = _safe_load_json(path)
            if not isinstance(data, dict):
                continue
            status = str(data.get("status") or "").strip()
            broker = str(data.get("broker_order_id") or "").strip()
            # Only true open submitted orders count as residual; pure
            # ``approved`` (no broker_order_id) is a dry-run artefact.
            if status in _OPEN_STATUS and broker:
                rep.orders_residual += 1
                if len(rep.orders_residual_sample) < 10:
                    rep.orders_residual_sample.append(path.name)

    py_bin = root / "venv" / "bin" / "python3"
    if not py_bin.exists():
        py_bin = Path(sys.executable)
    rc, py_ver, _ = _run([str(py_bin), "--version"], root)
    if rc == 0 and py_ver:
        rep.python_version = py_ver.split()[-1] if py_ver.split() else py_ver

    try:
        usage = shutil.disk_usage(str(root))
        rep.disk_free_gb = round(usage.free / (1024 ** 3), 2)
    except OSError:
        rep.disk_free_gb = None

    return rep


# ---------------------------------------------------------------------------
# Check engine
# ---------------------------------------------------------------------------

def evaluate_checks(
    rep_a: WorktreeReport,
    rep_b: WorktreeReport,
    *,
    expected_tag_a: str | None,
    expected_branch_b: str | None,
    min_disk_gb: float,
) -> list[CheckResult]:
    checks: list[CheckResult] = []

    # 1. Worktrees exist and are git repos.
    for rep in (rep_a, rep_b):
        if not rep.exists:
            checks.append(CheckResult(
                name=f"worktree_exists[{rep.label}]",
                status="fail",
                detail=f"path does not exist: {rep.path}",
            ))
        elif not rep.is_git:
            checks.append(CheckResult(
                name=f"worktree_is_git[{rep.label}]",
                status="fail",
                detail=f"not a git repository: {rep.path}",
            ))
        else:
            checks.append(CheckResult(
                name=f"worktree_exists[{rep.label}]",
                status="ok",
                detail=f"git repo at {rep.path}",
                extra={"head_sha": rep.head_sha, "branch": rep.branch_name},
            ))

    # 2. Expected git anchor for run A (tag).
    if expected_tag_a:
        if expected_tag_a in rep_a.head_tags:
            checks.append(CheckResult(
                name="expected_tag_a",
                status="ok",
                detail=f"HEAD points at tag '{expected_tag_a}'",
            ))
        else:
            checks.append(CheckResult(
                name="expected_tag_a",
                status="fail",
                detail=(
                    f"HEAD of run-a is not at tag '{expected_tag_a}'; "
                    f"current tags={rep_a.head_tags or '(none)'}, "
                    f"branch={rep_a.branch_name}"
                ),
            ))

    # 3. Expected branch for run B.
    if expected_branch_b:
        if rep_b.branch_name == expected_branch_b:
            checks.append(CheckResult(
                name="expected_branch_b",
                status="ok",
                detail=f"run-b on branch '{expected_branch_b}'",
            ))
        else:
            checks.append(CheckResult(
                name="expected_branch_b",
                status="fail",
                detail=(
                    f"run-b not on '{expected_branch_b}'; "
                    f"current branch={rep_b.branch_name}"
                ),
            ))

    # 4. Worktrees clean.
    for rep in (rep_a, rep_b):
        if not rep.is_git:
            continue
        if rep.is_dirty:
            checks.append(CheckResult(
                name=f"worktree_clean[{rep.label}]",
                status="fail",
                detail=(
                    f"{len(rep.dirty_files)} dirty entries; commit/stash before dual-run."
                ),
                extra={"sample": rep.dirty_files[:5]},
            ))
        else:
            checks.append(CheckResult(
                name=f"worktree_clean[{rep.label}]",
                status="ok",
                detail="git status clean",
            ))

    # 5. Config consistency.
    if rep_a.config_path is not None or rep_b.config_path is not None:
        if not rep_a.config_present:
            checks.append(CheckResult(
                name="config_present[a]",
                status="fail",
                detail=f"config not found in run-a: {rep_a.config_path}",
            ))
        if not rep_b.config_present:
            checks.append(CheckResult(
                name="config_present[b]",
                status="fail",
                detail=f"config not found in run-b: {rep_b.config_path}",
            ))
        if rep_a.config_present and rep_b.config_present:
            if rep_a.config_sha256 == rep_b.config_sha256:
                checks.append(CheckResult(
                    name="config_sha256_match",
                    status="ok",
                    detail=f"sha256={rep_a.config_sha256}",
                ))
            else:
                checks.append(CheckResult(
                    name="config_sha256_match",
                    status="fail",
                    detail=(
                        f"config drift: a={rep_a.config_sha256} "
                        f"b={rep_b.config_sha256}"
                    ),
                ))

    # 6. Orders residual.
    for rep in (rep_a, rep_b):
        if not rep.is_git:
            continue
        if rep.orders_residual == 0:
            checks.append(CheckResult(
                name=f"orders_no_residual[{rep.label}]",
                status="ok",
                detail="no open submitted orders carried over",
            ))
        else:
            checks.append(CheckResult(
                name=f"orders_no_residual[{rep.label}]",
                status="fail",
                detail=(
                    f"{rep.orders_residual} open submitted orders found in "
                    f"state/runs/orders/; reconcile or archive before dual-run."
                ),
                extra={"sample": rep.orders_residual_sample},
            ))

    # 7. Python major.minor identical.
    if rep_a.python_version and rep_b.python_version:
        ma = ".".join(rep_a.python_version.split(".")[:2])
        mb = ".".join(rep_b.python_version.split(".")[:2])
        if ma == mb:
            checks.append(CheckResult(
                name="python_major_minor_match",
                status="ok",
                detail=f"both on Python {ma} (a={rep_a.python_version}, b={rep_b.python_version})",
            ))
        else:
            checks.append(CheckResult(
                name="python_major_minor_match",
                status="warn",
                detail=(
                    f"Python minor mismatch: a={rep_a.python_version} "
                    f"b={rep_b.python_version}"
                ),
            ))

    # 8. Disk space.
    for rep in (rep_a, rep_b):
        if rep.disk_free_gb is None:
            checks.append(CheckResult(
                name=f"disk_free[{rep.label}]",
                status="warn",
                detail="could not stat disk usage",
            ))
        elif rep.disk_free_gb >= min_disk_gb:
            checks.append(CheckResult(
                name=f"disk_free[{rep.label}]",
                status="ok",
                detail=f"{rep.disk_free_gb:.2f} GB free (>= {min_disk_gb} GB)",
            ))
        else:
            checks.append(CheckResult(
                name=f"disk_free[{rep.label}]",
                status="fail",
                detail=(
                    f"only {rep.disk_free_gb:.2f} GB free; "
                    f"need >= {min_disk_gb} GB for events.jsonl + orders/"
                ),
            ))

    return checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Read-only preflight self-check before launching a SIM dual-run.",
    )
    p.add_argument("--run-a", required=True, type=Path,
                   help="Path to legacy worktree (e.g. /data/dual_run/legacy)")
    p.add_argument("--run-b", required=True, type=Path,
                   help="Path to vnpy-native worktree (e.g. /data/dual_run/vnpy_native)")
    p.add_argument("--name-a", type=str, default="legacy")
    p.add_argument("--name-b", type=str, default="vnpy_native")
    p.add_argument("--expected-tag-a", type=str, default="classic-pre-vnpy-rewrite-v1",
                   help="Tag that run-a HEAD must point at (skip with empty string)")
    p.add_argument("--expected-branch-b", type=str, default="classic-vnpy-native-rewrite",
                   help="Branch that run-b HEAD must be on (skip with empty string)")
    p.add_argument("--config", type=str, default="",
                   help="Relative path to the strategy config; checked in BOTH worktrees")
    p.add_argument("--min-disk-gb", type=float, default=_DEFAULT_MIN_DISK_GB,
                   help="Minimum free disk space in GB for each worktree")
    p.add_argument("--output", type=Path, default=None,
                   help="JSON output path; defaults to "
                        "state/runs/reports/preflight_<YYYYMMDD>.json under cwd")
    p.add_argument("--no-print", dest="print_summary", action="store_false", default=True)
    return p


def main() -> int:
    args = build_parser().parse_args()

    run_a = args.run_a.expanduser().resolve()
    run_b = args.run_b.expanduser().resolve()

    config_relpath = args.config or None
    rep_a = probe_worktree(args.name_a, run_a, config_relpath)
    rep_b = probe_worktree(args.name_b, run_b, config_relpath)

    expected_tag_a = args.expected_tag_a or None
    expected_branch_b = args.expected_branch_b or None

    checks = evaluate_checks(
        rep_a, rep_b,
        expected_tag_a=expected_tag_a,
        expected_branch_b=expected_branch_b,
        min_disk_gb=args.min_disk_gb,
    )

    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]

    output_path = args.output
    if output_path is None:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        output_path = Path("state/runs/reports") / f"preflight_{today}.json"

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_a": dataclasses.asdict(rep_a),
        "run_b": dataclasses.asdict(rep_b),
        "checks": [dataclasses.asdict(c) for c in checks],
        "totals": {
            "checks": len(checks),
            "ok": sum(1 for c in checks if c.status == "ok"),
            "warn": len(warns),
            "fail": len(fails),
        },
    }
    # Convert Path objects in run_a / run_b to str so json.dumps works.
    for run_key in ("run_a", "run_b"):
        d = summary[run_key]
        for k, v in list(d.items()):
            if isinstance(v, Path):
                d[k] = str(v)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"warning: could not write {output_path}: {exc}", file=sys.stderr)

    if args.print_summary:
        print(f"=== dual_run_preflight: {rep_a.label} vs {rep_b.label} ===")
        print(f" run_a: {rep_a.path}")
        print(f" run_b: {rep_b.path}")
        print(f" totals: ok={summary['totals']['ok']}  "
              f"warn={summary['totals']['warn']}  fail={summary['totals']['fail']}")
        print("--")
        for c in checks:
            icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(c.status, "❓")
            print(f" {icon} {c.name:<32} {c.detail}")
        if output_path:
            print(f"--\nfull report: {output_path}")

    if fails:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
