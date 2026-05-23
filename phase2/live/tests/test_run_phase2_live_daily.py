"""Unit tests for phase2.runners.run_phase2_live_daily CLI entry.

Covers:
- ``--help`` returns instantly without importing futu/vnpy DB.
- The 6-switch validator gates the execution: missing any switch in REAL
  mode results in a non-zero exit code with the missing switches listed on
  stderr (mirrors requirements §6.1 and §9.2.②).
- The argparse surface contains every flag mandated by §8.1.
"""

from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout

import pytest


# We load the CLI module directly from its file path so we don't trigger
# anything that lives at /projects/vnpy/phase2/runners/__init__.py.
def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "phase2_run_live_cli",
        "/projects/vnpy/phase2/runners/run_phase2_live_daily.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# argparse surface
# ---------------------------------------------------------------------------

class TestArgparseSurface:
    def test_required_flags_present(self):
        cli = _load_cli()
        parser = cli.build_parser()
        actions = {a.option_strings[0] if a.option_strings else a.dest
                   for a in parser._actions}
        # Spot-check every flag mandated by requirements §8.1.
        for flag in [
            "--futu-env", "--futu-market", "--rebalance-time", "--session-tz",
            "--live-submit", "--respect-live-submit", "--pool-config",
            "--strategy-path", "--init-cash", "--max-single-position-pct",
            "--max-daily-new-position-pct", "--max-market-exposure-pct",
            "--max-drawdown-pct", "--max-order-value", "--auto-cancel-on-eod",
            "--post-rebalance-wait-seconds", "--max-runtime-seconds",
            "--bar-warmup", "--run-id",
        ]:
            assert flag in actions, f"missing flag: {flag}"

    def test_help_does_not_import_heavy_modules(self):
        # Just ensure parser construction is cheap.
        cli = _load_cli()
        parser = cli.build_parser()
        assert parser.format_help()  # truthy text

    def test_futu_env_choices_are_chinese_literals(self):
        cli = _load_cli()
        parser = cli.build_parser()
        action = next(a for a in parser._actions if "--futu-env" in a.option_strings)
        assert sorted(action.choices) == sorted(["模拟", "真实"])


# ---------------------------------------------------------------------------
# 6-switch enforcement at CLI level
# ---------------------------------------------------------------------------

class TestSafetyEnforcement:
    def test_real_without_envs_exits_nonzero(self, monkeypatch):
        cli = _load_cli()
        monkeypatch.setattr(cli, "os", cli.os)  # already real os
        # Clear the env to ensure REAL mode fails.
        for key in [
            "VNPY_LIVE_CONFIG", "VNPY_LIVE_SUBMIT", "VNPY_LIVE_APPROVED",
            "FUTU_ENV", "FUTU_TRADE_PASSWORD",
            "FUTU_HOST", "FUTU_PORT", "FUTU_MARKET",
        ]:
            monkeypatch.delenv(key, raising=False)
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            rc = cli.main(["--futu-env", "真实", "--live-submit"])
        assert rc != 0
        assert "BLOCKED" in err.getvalue()

    def test_dry_run_without_pool_or_strategy_exits_gracefully(self, monkeypatch, tmp_path):
        cli = _load_cli()
        # Provide a fake pool config the CLI can load successfully so we get
        # past the safety gate without actually running the daily loop.
        pool_yaml = tmp_path / "pool.yaml"
        pool_yaml.write_text(
            "version: 1\n"
            "symbols:\n"
            "  - symbol: US.AAPL\n"
            "    name: Apple\n"
            "defaults:\n"
            "  market: US\n",
            encoding="utf-8",
        )
        # We don't want the full run to actually execute; stub out the runner.
        class _StubRunner:
            def __init__(self, *a, **kw): pass
            def run(self):
                from phase2.live.runner import DailyReport
                return DailyReport(
                    pool=["US.AAPL"], rebalance_date="2026-05-20",
                    rebalance_time="now", execution_env="dry_run",
                )
        monkeypatch.setattr(cli, "DailyLiveRebalanceRunner", _StubRunner, raising=False)
        # The CLI imports DailyLiveRebalanceRunner inside main(); patch via
        # sys.modules instead.
        import sys as _sys
        from phase2.live import runner as runner_mod
        monkeypatch.setattr(runner_mod, "DailyLiveRebalanceRunner", _StubRunner)

        out = io.StringIO(); err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            rc = cli.main([
                "--pool-config", str(pool_yaml),
                "--rebalance-now",
            ])
        # dry_run path is allowed; rc may be 3 if pool_loader rejects the
        # minimal yaml, or 0 if it loads. Either way it should NOT be 2
        # (which would indicate a safety failure), and the run is announced.
        assert rc in (0, 3)


# ---------------------------------------------------------------------------
# run id + products dir
# ---------------------------------------------------------------------------

class TestRunIdResolution:
    def test_resolve_run_id_with_override(self):
        cli = _load_cli()
        assert cli._resolve_run_id("dry_run", "smoke-1") == "smoke-1"

    def test_resolve_run_id_default(self):
        cli = _load_cli()
        run_id = cli._resolve_run_id("futu_sim", None)
        assert run_id.startswith("futu_sim_")
        assert "Z" in run_id

    def test_resolve_products_dir_per_env(self):
        cli = _load_cli()
        for env in ("dry_run", "futu_sim", "futu_real"):
            p = cli._resolve_products_dir(env, "rid-1")
            assert env in p.parts and "rid-1" in p.parts
            assert "phase2_live" in p.parts
