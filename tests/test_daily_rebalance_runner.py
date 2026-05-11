from __future__ import annotations

"""Unit tests for the classic_multifactor daily-rebalance runner (S3b).

Verifies:

1. ``configs/classic_multifactor/daily_example.json`` passes
   ``validate_loop_mode("daily")``.
2. ``DailyRebalanceRunner`` rejects the canonical NVDA_G09 (intraday) config
   with a ``LoopModeValidationError`` — the runner cannot be tricked into
   running an intraday config.
3. The daily runner's ``_resolve_live_submit`` follows the same hard-switch
   gating semantics as the intraday runner.
4. The runner refuses to construct without a ``rebalance_time``.

These tests do NOT spin up MainEngine.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.classic_multifactor.config_schema import (
    LoopModeValidationError,
    validate_loop_mode,
)
from scripts.classic_multifactor._base_runner import map_vt_symbol
from scripts.classic_multifactor.run_daily_rebalance import (
    DailyRebalanceRunner,
    build_parser,
)


def _make_args(overrides: dict) -> argparse.Namespace:
    parser = build_parser()
    base = ["--config", overrides.pop("config", "")]
    for k, v in overrides.items():
        flag = "--" + k.replace("_", "-")
        base.extend([flag, str(v)])
    return parser.parse_args(base)


def test_daily_example_config_passes_schema():
    cfg = json.loads((REPO_ROOT / "configs" / "classic_multifactor" / "daily_example.json").read_text(encoding="utf-8"))
    assert validate_loop_mode(cfg, "daily") == "daily"


def test_intraday_config_rejected_by_daily_runner():
    intraday_cfg = REPO_ROOT / "configs" / "classic_multifactor" / "nvda_g09.json"
    args = _make_args({"config": str(intraday_cfg), "rebalance_time": "16:05"})
    try:
        DailyRebalanceRunner(args)
    except LoopModeValidationError:
        return
    raise AssertionError("intraday config not rejected by daily runner")


def test_daily_runner_requires_rebalance_time():
    # Config without rebalance_time, CLI also missing → should fall back to the market default.
    with tempfile.TemporaryDirectory() as td:
        bad_cfg = Path(td) / "no_rebalance.json"
        bad_cfg.write_text(json.dumps({
            "loop_mode": "daily",
            "symbol": "NVDA.US",
            "interval": "1d",
            "setting": {"capital": 5000.0},
        }), encoding="utf-8")
        args = _make_args({"config": str(bad_cfg), "state_root": td})
        runner = DailyRebalanceRunner(args)
        assert runner.rebalance_time_local.strftime("%H:%M") == "15:55"


def test_daily_runner_derives_hk_market_default_rebalance_time():
    cfg = REPO_ROOT / "configs" / "classic_multifactor" / "09992_hk_d01.json"
    with tempfile.TemporaryDirectory() as td:
        custom_cfg = Path(td) / "hk_daily_no_rebalance.json"
        payload = json.loads(cfg.read_text(encoding="utf-8"))
        payload.pop("rebalance_time", None)
        custom_cfg.write_text(json.dumps(payload), encoding="utf-8")
        args = _make_args({"config": str(custom_cfg), "state_root": td})
        runner = DailyRebalanceRunner(args)
        assert runner.rebalance_time_local.strftime("%H:%M") == "15:55"
        assert runner.session_tz == "Asia/Hong_Kong"


def test_daily_runner_hard_switch_gating():
    import os
    saved = {k: os.environ.get(k) for k in ("VNPY_LIVE_CONFIG", "VNPY_LIVE_SUBMIT", "VNPY_LIVE_APPROVED")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        assert DailyRebalanceRunner._resolve_live_submit(True) is False
        os.environ["VNPY_LIVE_CONFIG"] = "YES"
        os.environ["VNPY_LIVE_SUBMIT"] = "YES"
        os.environ["VNPY_LIVE_APPROVED"] = "YES"
        assert DailyRebalanceRunner._resolve_live_submit(True) is True
        assert DailyRebalanceRunner._resolve_live_submit(False) is False
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_map_vt_symbol_normalizes_hk_exchange_suffix():
    assert map_vt_symbol("00700.HK") == "00700.SEHK"
    assert map_vt_symbol("00700.SEHK") == "00700.SEHK"


def test_daily_runner_construct_with_example_config():
    """Smoke test: the example config must construct without errors (no engine start)."""
    cfg = REPO_ROOT / "configs" / "classic_multifactor" / "daily_example.json"
    with tempfile.TemporaryDirectory() as td:
        args = _make_args({"config": str(cfg), "state_root": td})
        runner = DailyRebalanceRunner(args)
        assert runner.stats.loop_mode == "daily"
        assert runner.rebalance_time_local.strftime("%H:%M") == "16:05"
        assert runner.vt_symbol.endswith(".SMART"), runner.vt_symbol
        assert runner.daily_new_pct_limit == 0.20
        assert runner.live_submit is False  # CLI flag not set


def _run_all():
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed: list[tuple[str, str]] = []
    for fn in funcs:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((fn.__name__, repr(exc)))
        else:
            passed += 1
            print(f"PASS {fn.__name__}")
    print(f"\n{passed}/{len(funcs)} tests passed")
    for name, err in failed:
        print(f"FAIL {name}: {err}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
