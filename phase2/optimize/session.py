"""Session paths & ID utilities for phase2 strategy self-optimization."""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Anchored on repo root: state/runs/<NS>/<session_id>/
DEFAULT_NAMESPACE = "phase2_strategy_self_optimize"
# Sibling namespace whose run_ids must never collide with our session IDs.
SIBLING_NAMESPACE = "phase2_multi_backtest"

# Default repo root assumes <repo>/state/runs/...; callers can override.
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

_SESSION_ID_RE = re.compile(r"^opt_\d{8}T\d{6}Z$")


def generate_session_id(now: _dt.datetime | None = None) -> str:
    """Build a session id like ``opt_20260523T133501Z`` (UTC)."""
    if now is None:
        now = _dt.datetime.utcnow()
    return now.strftime("opt_%Y%m%dT%H%M%SZ")


def is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.match(session_id))


@dataclass(frozen=True)
class SessionPaths:
    """All on-disk paths owned by a single optimization session.

    The convention guarantees full isolation from
    ``state/runs/phase2_multi_backtest/``: every produced file lives
    strictly under :pyattr:`session_dir`.
    """

    session_id: str
    repo_root: Path
    namespace: str = DEFAULT_NAMESPACE

    @property
    def runs_root(self) -> Path:
        return self.repo_root / "state" / "runs"

    @property
    def namespace_dir(self) -> Path:
        return self.runs_root / self.namespace

    @property
    def sibling_dir(self) -> Path:
        return self.runs_root / SIBLING_NAMESPACE

    @property
    def session_dir(self) -> Path:
        return self.namespace_dir / self.session_id

    @property
    def progress_log(self) -> Path:
        return self.session_dir / "progress.log"

    @property
    def session_summary_json(self) -> Path:
        return self.session_dir / "session_summary.json"

    @property
    def report_md(self) -> Path:
        return self.session_dir / "REPORT.md"

    @property
    def baseline_link(self) -> Path:
        return self.session_dir / "best_trial"

    # ---- per-iteration / per-trial helpers ---------------------------
    def iter_dir(self, iter_idx: int) -> Path:
        return self.session_dir / f"iter_{iter_idx:02d}"

    def proposals_json(self, iter_idx: int) -> Path:
        return self.iter_dir(iter_idx) / "proposals.json"

    def evaluations_json(self, iter_idx: int) -> Path:
        return self.iter_dir(iter_idx) / "evaluations.json"

    def leaderboard_csv(self, iter_idx: int) -> Path:
        return self.iter_dir(iter_idx) / "leaderboard.csv"

    def cumulative_leaderboard_csv(self) -> Path:
        return self.session_dir / "leaderboard.csv"

    def trial_dir(self, iter_idx: int, trial_idx: int) -> Path:
        return self.iter_dir(iter_idx) / f"trial_{trial_idx:02d}"

    # ---- factory & lifecycle -----------------------------------------
    @classmethod
    def create(
        cls,
        session_id: str | None = None,
        repo_root: Path | None = None,
        namespace: str = DEFAULT_NAMESPACE,
        *,
        now: _dt.datetime | None = None,
    ) -> "SessionPaths":
        sid = session_id or generate_session_id(now=now)
        if not is_valid_session_id(sid):
            raise ValueError(f"invalid session_id: {sid!r}")
        root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
        return cls(session_id=sid, repo_root=root, namespace=namespace)

    def ensure_dirs(self) -> None:
        """Create the session_dir; refuse if it conflicts with a sibling run."""
        self._assert_no_sibling_collision()
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def ensure_iter_dirs(self, iter_idx: int, trial_count: int) -> None:
        for trial_idx in range(trial_count):
            self.trial_dir(iter_idx, trial_idx).mkdir(parents=True, exist_ok=True)

    def _assert_no_sibling_collision(self) -> None:
        """Refuse if the session id matches any existing run_id under
        ``state/runs/phase2_multi_backtest/``.

        This protects against accidentally writing into / shadowing an
        existing multi-backtest artefact. (Requirement 1.5)
        """
        sibling = self.sibling_dir
        if not sibling.exists():
            return
        for child in sibling.iterdir():
            if child.is_dir() and child.name == self.session_id:
                raise RuntimeError(
                    "session_id collision with phase2_multi_backtest run: "
                    f"{child}"
                )

    def disk_free_bytes(self) -> int:
        target = self.namespace_dir if self.namespace_dir.exists() else self.runs_root
        usage = shutil.disk_usage(str(target))
        return int(usage.free)


def list_existing_sessions(
    repo_root: Path | None = None,
    namespace: str = DEFAULT_NAMESPACE,
) -> list[str]:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    ns_dir = root / "state" / "runs" / namespace
    if not ns_dir.exists():
        return []
    return sorted(
        p.name
        for p in ns_dir.iterdir()
        if p.is_dir() and is_valid_session_id(p.name)
    )


def find_latest_iter(session: SessionPaths) -> int:
    """Return the highest existing iter index under the session, or -1."""
    if not session.session_dir.exists():
        return -1
    indices: list[int] = []
    for child in session.session_dir.iterdir():
        if child.is_dir() and child.name.startswith("iter_"):
            try:
                indices.append(int(child.name.split("_", 1)[1]))
            except (IndexError, ValueError):
                continue
    return max(indices) if indices else -1


__all__ = [
    "DEFAULT_NAMESPACE",
    "SIBLING_NAMESPACE",
    "SessionPaths",
    "find_latest_iter",
    "generate_session_id",
    "is_valid_session_id",
    "list_existing_sessions",
]
