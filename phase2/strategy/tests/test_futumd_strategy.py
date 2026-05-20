"""Tests for the futumd-compatible single-file strategy.

Hard contract — this strategy is *meant* to be uploaded to the Futu
platform as-is.  Unit tests therefore enforce:

1) Zero local imports — no ``from phase2.*`` / ``from services.*`` /
   ``import phase2`` / ``import services`` statements anywhere.
2) The 5 lifecycle methods + ``_enter_position`` / ``_exit_position``
   exist on ``Strategy`` (NVDA reference parity).
3) The pool size declared via ``trigger_symbols`` <= 20.
4) ``LIVE_SUBMIT`` defaults to False.
5) Pool symbols match ``pool_config.yaml`` (so phase ② dry-run runner
   and the futumd file stay in sync).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import List

import pytest
import yaml

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FUTUMD_PATH = _REPO_ROOT / "phase2" / "strategy" / "us_multi_symbol_phase2_strategy_futumd.py"
POOL_CFG_PATH = _REPO_ROOT / "phase2" / "strategy" / "config" / "pool_config.yaml"


def _read_ast() -> ast.Module:
    return ast.parse(FUTUMD_PATH.read_text(encoding="utf-8"), str(FUTUMD_PATH))


def test_no_local_phase2_or_services_imports():
    """Strategy file must not import from local repo packages."""

    tree = _read_ast()
    forbidden_prefixes = ("phase2", "services", "vnpy_evolution")
    bad: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            head = node.module.split(".")[0]
            if head in forbidden_prefixes:
                bad.append("from {m} import ...".format(m=node.module))
        elif isinstance(node, ast.Import):
            for n in node.names:
                head = n.name.split(".")[0]
                if head in forbidden_prefixes:
                    bad.append("import {m}".format(m=n.name))
    assert not bad, "futumd strategy must NOT import local packages: {b}".format(b=bad)


def test_lifecycle_methods_present():
    """All 5 lifecycle hooks + _enter_position / _exit_position exist."""

    tree = _read_ast()
    klass = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.ClassDef) and n.name == "Strategy"),
        None,
    )
    assert klass is not None, "Strategy class missing"
    methods = {n.name for n in klass.body if isinstance(n, ast.FunctionDef)}
    required = {
        "initialize", "trigger_symbols", "custom_indicator",
        "global_variables", "handle_data",
        "_enter_position", "_exit_position",
        "_sma", "_rsi", "_volume_ratio", "_atr_pct",
    }
    missing = required - methods
    assert not missing, "missing methods: {m}".format(m=missing)


def test_only_strategy_class_no_module_helpers():
    """Per design: no module-level helper functions; everything is a method."""

    tree = _read_ast()
    module_level_funcs = [
        n.name for n in tree.body if isinstance(n, ast.FunctionDef)
    ]
    # Stub functions inside the ``except NameError`` block sit *inside* the
    # try/except, not at module level — so this list must stay empty.
    assert module_level_funcs == [], (
        "module-level functions are forbidden; found: {f}".format(
            f=module_level_funcs
        )
    )


def _import_strategy_module():
    sys.path.insert(0, str(_REPO_ROOT / "phase2" / "strategy"))
    import importlib
    if "us_multi_symbol_phase2_strategy_futumd" in sys.modules:
        del sys.modules["us_multi_symbol_phase2_strategy_futumd"]
    return importlib.import_module("us_multi_symbol_phase2_strategy_futumd")


def test_pool_size_within_hard_cap_and_matches_yaml():
    mod = _import_strategy_module()
    s = mod.Strategy()
    s.initialize()
    assert len(s._pool) <= 20, "pool size exceeds project hard cap 20"
    assert len(s._targets) == len(s._pool), "trigger declarations mismatch"

    # Cross-check with pool_config.yaml so both lists stay in sync.
    raw = yaml.safe_load(POOL_CFG_PATH.read_text(encoding="utf-8"))
    yaml_syms = sorted(item["symbol"].upper() for item in raw["symbols"])
    py_syms = sorted(s.upper() for s in s._pool)
    assert py_syms == yaml_syms, (
        "futumd pool ≠ pool_config.yaml: futumd={p} yaml={y}".format(
            p=py_syms, y=yaml_syms
        )
    )


def test_live_submit_defaults_false():
    mod = _import_strategy_module()
    s = mod.Strategy()
    s.initialize()
    assert s.LIVE_SUBMIT is False, "LIVE_SUBMIT must ship False"


def test_state_dict_initialised_for_each_symbol():
    mod = _import_strategy_module()
    s = mod.Strategy()
    s.initialize()
    for sym in s._pool:
        assert sym in s._state, "missing state for {s}".format(s=sym)
        st = s._state[sym]
        for f in ("entry_price", "used_slices", "highest_price",
                  "bars_since_last_entry", "bars_since_last_exit"):
            assert f in st, "state[{s}] missing {f}".format(s=sym, f=f)
