from __future__ import annotations

from importlib import util
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "quant_workflow" / "run_holdings_knot_review.py"
SPEC = util.spec_from_file_location("run_holdings_knot_review", MODULE_PATH)
assert SPEC and SPEC.loader
holdings_review = util.module_from_spec(SPEC)
sys.modules[SPEC.name] = holdings_review
SPEC.loader.exec_module(holdings_review)


def test_read_account_summary_forwards_real_env_and_live_strict(monkeypatch):
    captured: dict[str, object] = {}

    class DummyProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get_summary(self):
            return {"ok": True}

    monkeypatch.setattr(holdings_review, "FutuAccountProvider", DummyProvider)

    summary = holdings_review._read_account_summary(trd_env="REAL", live_strict=True)

    assert summary == {"ok": True}
    assert captured["expect_trd_env"] == "REAL"
    assert captured["live_strict"] is True
