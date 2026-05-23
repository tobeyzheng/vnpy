"""Unit tests for phase2.live.safety.

Covers every branch of the 6-switch validator. Each test uses an explicit
``env`` mapping so the live process environment never leaks into the suite.
"""

from __future__ import annotations

import pytest

from phase2.live.safety import (
    REAL_REQUIRED_ENV_FLAGS,
    SIM_REQUIRED_ENV_KEYS,
    SafetyDecision,
    validate_safety,
)


SIM_GOOD_ENV: dict[str, str] = {
    "FUTU_HOST": "127.0.0.1",
    "FUTU_PORT": "11111",
    "FUTU_MARKET": "US",
}

REAL_GOOD_ENV: dict[str, str] = {
    **SIM_GOOD_ENV,
    "VNPY_LIVE_CONFIG": "YES",
    "VNPY_LIVE_SUBMIT": "YES",
    "VNPY_LIVE_APPROVED": "YES",
    "FUTU_ENV": "真实",
    "FUTU_TRADE_PASSWORD": "secret-not-logged",
}


# ---------------------------------------------------------------------------
# dry_run path
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_no_flags_is_dry_run(self):
        d = validate_safety(futu_env_cli=None, live_submit=False, env={})
        assert d.allowed is True
        assert d.execution_env == "dry_run"
        assert d.missing == []

    def test_futu_env_without_live_submit_warns(self):
        d = validate_safety(futu_env_cli="模拟", live_submit=False, env=SIM_GOOD_ENV)
        assert d.allowed is True
        assert d.execution_env == "dry_run"
        assert any("--live-submit" in w for w in d.warnings)

    def test_unknown_futu_env_falls_back_to_dry_run_then_blocked(self):
        # Unknown --futu-env + --live-submit ⇒ dry_run resolution, then
        # safety is allowed (dry_run has no prerequisites). This is the
        # *intentional* fail-safe for typo-ed env values.
        d = validate_safety(futu_env_cli="prod", live_submit=True, env=REAL_GOOD_ENV)
        assert d.execution_env == "dry_run"
        assert d.allowed is True


# ---------------------------------------------------------------------------
# futu_sim path
# ---------------------------------------------------------------------------

class TestSim:
    def test_sim_happy_path(self):
        d = validate_safety(
            futu_env_cli="模拟", live_submit=True, env=SIM_GOOD_ENV
        )
        assert d.allowed is True
        assert d.execution_env == "futu_sim"
        assert d.missing == []
        assert "--live-submit" in d.checked

    def test_sim_english_alias(self):
        for alias in ("sim", "Simulate", "simulated", "SIM"):
            d = validate_safety(
                futu_env_cli=alias, live_submit=True, env=SIM_GOOD_ENV
            )
            assert d.execution_env == "futu_sim"
            assert d.allowed is True

    @pytest.mark.parametrize("missing_key", list(SIM_REQUIRED_ENV_KEYS))
    def test_sim_missing_each_env(self, missing_key):
        env = dict(SIM_GOOD_ENV)
        env.pop(missing_key)
        d = validate_safety(futu_env_cli="模拟", live_submit=True, env=env)
        assert d.allowed is False
        assert any(missing_key in m for m in d.missing)

    def test_sim_market_must_be_us(self):
        env = {**SIM_GOOD_ENV, "FUTU_MARKET": "HK"}
        d = validate_safety(futu_env_cli="模拟", live_submit=True, env=env)
        assert d.allowed is False
        assert any("FUTU_MARKET" in m for m in d.missing)

    def test_sim_does_not_require_real_envs(self):
        d = validate_safety(
            futu_env_cli="模拟", live_submit=True, env=SIM_GOOD_ENV
        )
        # No REAL flags should appear in 'checked' under SIM.
        for key in REAL_REQUIRED_ENV_FLAGS:
            assert all(key not in c for c in d.checked), d.checked

    def test_sim_unused_real_envs_emit_warnings(self):
        env = {**SIM_GOOD_ENV, "FUTU_TRADE_PASSWORD": "leak"}
        d = validate_safety(futu_env_cli="模拟", live_submit=True, env=env)
        assert d.allowed is True
        assert any("FUTU_TRADE_PASSWORD" in w for w in d.warnings)

    def test_sim_without_live_submit_blocks(self):
        d = validate_safety(
            futu_env_cli="模拟", live_submit=False, env=SIM_GOOD_ENV
        )
        # Falls back to dry_run by resolution rule → allowed under dry_run.
        # But warnings should hint --live-submit missing.
        assert d.execution_env == "dry_run"
        assert d.allowed is True
        assert any("--live-submit" in w for w in d.warnings)


# ---------------------------------------------------------------------------
# futu_real path
# ---------------------------------------------------------------------------

class TestReal:
    def test_real_happy_path(self):
        d = validate_safety(
            futu_env_cli="真实", live_submit=True, env=REAL_GOOD_ENV
        )
        assert d.allowed is True
        assert d.execution_env == "futu_real"
        assert d.missing == []
        # Every of the 4 REAL flags should appear in 'checked'.
        for key in REAL_REQUIRED_ENV_FLAGS:
            assert any(key in c for c in d.checked)
        assert any("FUTU_TRADE_PASSWORD" in c for c in d.checked)

    def test_real_english_alias(self):
        d = validate_safety(
            futu_env_cli="real", live_submit=True, env=REAL_GOOD_ENV
        )
        assert d.execution_env == "futu_real"
        assert d.allowed is True

    @pytest.mark.parametrize(
        "missing_env",
        list(REAL_REQUIRED_ENV_FLAGS) + ["FUTU_TRADE_PASSWORD"],
    )
    def test_real_missing_any_env_blocks(self, missing_env):
        env = dict(REAL_GOOD_ENV)
        env.pop(missing_env)
        d = validate_safety(futu_env_cli="真实", live_submit=True, env=env)
        assert d.allowed is False
        assert any(missing_env in m for m in d.missing)

    @pytest.mark.parametrize(
        "key,wrong_value",
        [
            ("VNPY_LIVE_CONFIG", "yes"),  # case-sensitive: must be exactly YES
            ("VNPY_LIVE_SUBMIT", "1"),
            ("VNPY_LIVE_APPROVED", "true"),
            ("FUTU_ENV", "real"),  # must be exactly "真实", not the english alias
        ],
    )
    def test_real_wrong_value_blocks(self, key, wrong_value):
        env = {**REAL_GOOD_ENV, key: wrong_value}
        d = validate_safety(futu_env_cli="真实", live_submit=True, env=env)
        assert d.allowed is False
        assert any(key in m for m in d.missing)

    def test_real_without_live_submit_falls_back_to_dry_run(self):
        d = validate_safety(
            futu_env_cli="真实", live_submit=False, env=REAL_GOOD_ENV
        )
        # No --live-submit ⇒ dry_run; safer than partial REAL.
        assert d.execution_env == "dry_run"
        assert d.allowed is True

    def test_real_empty_password_blocks(self):
        env = {**REAL_GOOD_ENV, "FUTU_TRADE_PASSWORD": ""}
        d = validate_safety(futu_env_cli="真实", live_submit=True, env=env)
        assert d.allowed is False
        assert any("FUTU_TRADE_PASSWORD" in m for m in d.missing)


# ---------------------------------------------------------------------------
# SafetyDecision shape
# ---------------------------------------------------------------------------

def test_decision_explain_does_not_leak_secrets():
    env = {**REAL_GOOD_ENV, "FUTU_TRADE_PASSWORD": "do-not-log-me"}
    d = validate_safety(futu_env_cli="真实", live_submit=True, env=env)
    text = d.explain()
    assert "do-not-log-me" not in text
    assert "secret" not in text.lower()


def test_decision_blocked_explain_lists_missing():
    env = dict(REAL_GOOD_ENV)
    env.pop("VNPY_LIVE_APPROVED")
    d = validate_safety(futu_env_cli="真实", live_submit=True, env=env)
    text = d.explain()
    assert "BLOCKED" in text
    assert "VNPY_LIVE_APPROVED" in text


def test_decision_is_frozen():
    d = SafetyDecision(allowed=True, execution_env="dry_run")
    with pytest.raises(Exception):
        d.allowed = False  # type: ignore[misc]
