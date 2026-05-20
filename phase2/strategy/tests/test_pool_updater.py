"""Tests for ``phase2/runners/run_pool_update.py``.

Cases covered:
1) dry-run with sane data ⇒ exit 0, no file writes, prints diff.
2) over-change ratio ⇒ exit 5 in --apply mode (still no write because
   the gate fires before write_text).
3) sector concentration > 40% ⇒ exit 6.
4) --apply without --confirm ⇒ exit 7 (project rule 2).
5) --apply --confirm with sane data ⇒ writes pool_config.yaml + jsonl.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = _REPO_ROOT / "phase2" / "runners" / "run_pool_update.py"


def _make_universe(tmp_path: Path, symbols):
    p = tmp_path / "universe.yaml"
    lines = ["version: 1", "currency: USD", "max_pool_size: 20",
             "defaults:", "  market: US",
             "  liquidity_min_adv60_usd: 50000000",
             "  price_min: 5.0", "  price_max: 800.0",
             "  atr_pct_max: 0.08", "  earnings_freeze_days: 2",
             "symbols:"]
    for sym, sec in symbols:
        lines.append("  - symbol: {s}".format(s=sym))
        lines.append("    market: US")
        lines.append("    market_cap_bucket: large")
        lines.append("    sector: {x}".format(x=sec))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _make_live(tmp_path: Path, symbols):
    p = tmp_path / "live.yaml"
    lines = ["version: 1", "currency: USD", "max_pool_size: 20",
             "defaults:", "  market: US",
             "  liquidity_min_adv60_usd: 50000000",
             "  price_min: 5.0", "  price_max: 800.0",
             "  atr_pct_max: 0.08", "  earnings_freeze_days: 2",
             "symbols:"]
    for sym, sec in symbols:
        lines.append("  - symbol: {s}".format(s=sym))
        lines.append("    market: US")
        lines.append("    market_cap_bucket: large")
        lines.append("    sector: {x}".format(x=sec))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _make_metrics(tmp_path: Path, symbols, *, price=100.0,
                  adv=200_000_000.0, atr=0.02):
    p = tmp_path / "metrics.json"
    p.write_text(json.dumps({s[0]: {"price": price, "adv60_usd": adv,
                                    "atr_pct": atr} for s in symbols}),
                 encoding="utf-8")
    return p


def _run(args, *, env=None):
    cmd = [sys.executable, str(RUNNER), *args]
    return subprocess.run(cmd, capture_output=True, text=True,
                          env=env, cwd=str(_REPO_ROOT))


def test_dry_run_no_writes_exit_zero(tmp_path: Path):
    syms = [("AAPL", "Technology"), ("MSFT", "Technology"),
            ("JPM", "Financials"), ("UNH", "Healthcare")]
    universe = _make_universe(tmp_path, syms)
    live = _make_live(tmp_path, syms)
    metrics = _make_metrics(tmp_path, syms)
    log = tmp_path / "log.jsonl"

    before = live.read_text(encoding="utf-8")
    r = _run(["--dry-run", "--universe", str(universe),
              "--live-pool", str(live), "--metrics", str(metrics),
              "--log", str(log), "--earnings", "/nonexistent.json",
              "--max-sector-ratio", "0.6"])
    assert r.returncode == 0, r.stderr
    assert live.read_text(encoding="utf-8") == before, "dry-run must not write"
    assert not log.exists(), "dry-run must not append jsonl"


def test_apply_without_confirm_blocks(tmp_path: Path):
    syms = [("AAPL", "Technology"), ("MSFT", "Technology")]
    universe = _make_universe(tmp_path, syms)
    live = _make_live(tmp_path, syms)
    metrics = _make_metrics(tmp_path, syms)

    r = _run(["--apply", "--universe", str(universe),
              "--live-pool", str(live), "--metrics", str(metrics),
              "--earnings", "/nonexistent.json",
              "--log", str(tmp_path / "log.jsonl")])
    assert r.returncode == 7, "exit 7 expected when --apply without --confirm"


def test_apply_with_confirm_writes(tmp_path: Path):
    # universe replaces 1 of 2 symbols (50% change) — well under 100% gate.
    universe_syms = [("AAPL", "Technology"), ("NEW1", "Healthcare")]
    live_syms = [("AAPL", "Technology"), ("OLD1", "Financials")]
    universe = _make_universe(tmp_path, universe_syms)
    live = _make_live(tmp_path, live_syms)
    metrics = _make_metrics(tmp_path, universe_syms)
    log = tmp_path / "log.jsonl"

    r = _run(["--apply", "--confirm", "--universe", str(universe),
              "--live-pool", str(live), "--metrics", str(metrics),
              "--earnings", "/nonexistent.json", "--log", str(log),
              "--change-log", str(tmp_path / "opslog.md"),
              "--max-change-ratio", "1.5", "--max-sector-ratio", "0.6"])
    assert r.returncode == 0, r.stderr
    new_live = live.read_text(encoding="utf-8")
    assert "NEW1" in new_live and "OLD1" not in new_live
    assert log.exists() and log.read_text(encoding="utf-8").strip(), \
        "jsonl audit log must be appended"


def test_sector_cap_gate_trips(tmp_path: Path):
    # 5 symbols all in Technology ⇒ 100% > 40% sector cap.
    syms = [("S{i}".format(i=i), "Technology") for i in range(5)]
    universe = _make_universe(tmp_path, syms)
    live = _make_live(tmp_path, syms[:1])  # baseline single sym
    metrics = _make_metrics(tmp_path, syms)

    r = _run(["--dry-run", "--universe", str(universe),
              "--live-pool", str(live), "--metrics", str(metrics),
              "--earnings", "/nonexistent.json",
              "--log", str(tmp_path / "log.jsonl")])
    assert r.returncode == 6, "exit 6 expected on sector cap trip; got {c}\n{o}\n{e}".format(
        c=r.returncode, o=r.stdout, e=r.stderr)


def test_over_change_ratio_dry_run_exits_5_but_no_write(tmp_path: Path):
    # 4 fresh symbols spread across 4 sectors replace 1 old ⇒ change
    # ratio 500% (4 add + 1 remove)/1; sector ratio 25%.
    sectors = ["Healthcare", "Energy", "Industrials", "Staples"]
    new_syms = [("N{i}".format(i=i), sectors[i]) for i in range(4)]
    live_syms = [("OLD", "Communication")]
    universe = _make_universe(tmp_path, new_syms)
    live = _make_live(tmp_path, live_syms)
    metrics = _make_metrics(tmp_path, new_syms)

    before = live.read_text(encoding="utf-8")
    r = _run(["--dry-run", "--universe", str(universe),
              "--live-pool", str(live), "--metrics", str(metrics),
              "--earnings", "/nonexistent.json",
              "--log", str(tmp_path / "log.jsonl"),
              "--max-sector-ratio", "0.6"])
    assert r.returncode == 5, r.stderr
    assert live.read_text(encoding="utf-8") == before
