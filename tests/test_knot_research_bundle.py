from __future__ import annotations

from datetime import datetime, timezone
from importlib import util
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "quant_workflow" / "run_knot_research_bundle.py"
SPEC = util.spec_from_file_location("run_knot_research_bundle", MODULE_PATH)
assert SPEC and SPEC.loader
bundle = util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bundle
SPEC.loader.exec_module(bundle)


def test_scheduled_task_due_for_hk_weekday_morning():
    task = next(item for item in bundle.SCHEDULED_TASKS if item.task_id == "hk_picks")
    now_utc = datetime(2026, 5, 12, 1, 5, tzinfo=timezone.utc)

    due, slot_key, local_now = bundle._scheduled_task_due(
        task,
        now_utc=now_utc,
        executed_slots={},
        window_seconds=3600,
    )

    assert due is True
    assert slot_key == "hk_picks:2026-05-12"
    assert local_now is not None
    assert local_now.strftime("%Y-%m-%d %H:%M") == "2026-05-12 09:05"


def test_scheduled_task_due_for_hk_midday_review():
    task = next(item for item in bundle.SCHEDULED_TASKS if item.task_id == "holdings_hk_noon")
    now_utc = datetime(2026, 5, 12, 4, 10, tzinfo=timezone.utc)

    due, slot_key, local_now = bundle._scheduled_task_due(
        task,
        now_utc=now_utc,
        executed_slots={},
        window_seconds=3600,
    )

    assert due is True
    assert slot_key == "holdings_hk_noon:2026-05-12"
    assert local_now is not None
    assert local_now.strftime("%Y-%m-%d %H:%M") == "2026-05-12 12:10"


def test_scheduled_task_not_due_on_weekend():
    task = next(item for item in bundle.SCHEDULED_TASKS if item.task_id == "hk_picks")
    now_utc = datetime(2026, 5, 16, 1, 5, tzinfo=timezone.utc)

    due, slot_key, local_now = bundle._scheduled_task_due(
        task,
        now_utc=now_utc,
        executed_slots={},
        window_seconds=3600,
    )

    assert due is False
    assert slot_key is None
    assert local_now is not None
    assert local_now.weekday() >= 5


def test_scheduled_task_not_due_twice_same_day():
    task = next(item for item in bundle.SCHEDULED_TASKS if item.task_id == "us_picks")
    now_utc = datetime(2026, 5, 12, 13, 10, tzinfo=timezone.utc)

    due, slot_key, _local_now = bundle._scheduled_task_due(
        task,
        now_utc=now_utc,
        executed_slots={"us_picks": "us_picks:2026-05-12"},
        window_seconds=3600,
    )

    assert due is False
    assert slot_key == "us_picks:2026-05-12"


def test_scheduled_task_due_for_us_midday_review():
    task = next(item for item in bundle.SCHEDULED_TASKS if item.task_id == "holdings_us_noon")
    now_utc = datetime(2026, 5, 12, 16, 5, tzinfo=timezone.utc)

    due, slot_key, local_now = bundle._scheduled_task_due(
        task,
        now_utc=now_utc,
        executed_slots={},
        window_seconds=3600,
    )

    assert due is True
    assert slot_key == "holdings_us_noon:2026-05-12"
    assert local_now is not None
    assert local_now.strftime("%Y-%m-%d %H:%M") == "2026-05-12 12:05"


def test_build_command_for_holdings_defaults_to_real_live_strict():
    command = bundle._build_command(
        script_name="run_holdings_knot_review.py",
        filename="holdings_knot_review.json",
        output_dir=Path("/tmp/knot"),
        per_dim=None,
        skip_knot=False,
        dry_run=False,
        holdings_trd_env="REAL",
        holdings_live_strict=True,
    )

    assert "--trd-env" in command
    assert "REAL" in command
    assert "--live-strict" in command
    assert "--output" in command


def test_build_command_for_holdings_supports_simulate_and_skip_knot():
    command = bundle._build_command(
        script_name="run_holdings_knot_review.py",
        filename="holdings_knot_review.json",
        output_dir=Path("/tmp/knot"),
        per_dim=None,
        skip_knot=True,
        dry_run=True,
        holdings_trd_env="SIMULATE",
        holdings_live_strict=False,
    )

    assert "--trd-env" in command
    assert "SIMULATE" in command
    assert "--live-strict" not in command
    assert "--skip-knot" in command
    assert "--dry-run" in command


def test_immediate_tasks_match_current_bundle_order():
    task_ids = [task.task_id for task in bundle.IMMEDIATE_TASKS]
    assert task_ids == ["hk_picks", "us_picks", "holdings_review"]
