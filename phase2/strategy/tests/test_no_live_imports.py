"""Hard-isolation guard: phase2.optimize must NEVER pull in futu / phase2.live.

Each test spawns a *fresh* Python subprocess so that module bookkeeping
state (sys.modules) is uncontaminated by other tests in the same session.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


_SCRIPT_TEMPLATE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {repo!r})
    {imports}
    forbidden_prefixes = ('futu', 'phase2.live')
    leaked = sorted(
        name for name in sys.modules
        if any(name == p or name.startswith(p + '.') for p in forbidden_prefixes)
    )
    if leaked:
        print('LEAK:' + ','.join(leaked))
        sys.exit(1)
    print('OK')
    """
).strip()


def _run_isolated(import_block: str) -> tuple[int, str]:
    script = _SCRIPT_TEMPLATE.format(repo=str(REPO_ROOT), imports=import_block)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


@pytest.mark.parametrize(
    "import_block",
    [
        "import phase2.optimize",
        "from phase2.optimize import session, io_schemas, search_space",
        "from phase2.optimize import optimizer, evaluator",
        "from phase2.optimize import trial_runner",
        "from phase2.optimize import coordinator",
        "from phase2.optimize import reporter",
        "from phase2.optimize import llm_bridge",
    ],
    ids=[
        "package",
        "session_io_search",
        "optimizer_evaluator",
        "trial_runner",
        "coordinator",
        "reporter",
        "llm_bridge",
    ],
)
def test_no_live_imports(import_block: str) -> None:
    rc, output = _run_isolated(import_block)
    assert rc == 0, f"forbidden import detected ({import_block}): {output.strip()}"
    assert "OK" in output


def test_assert_no_live_imports_helper() -> None:
    """``phase2.optimize.assert_no_live_imports`` must be callable
    and must NOT raise when called in a clean process."""
    rc, output = _run_isolated(
        "import phase2.optimize as o; o.assert_no_live_imports()"
    )
    assert rc == 0, output
    assert "OK" in output


def test_runner_module_no_live_imports() -> None:
    """Importing the CLI runner must not pull in live/futu either."""
    rc, output = _run_isolated(
        "import importlib.util, pathlib, sys\n"
        "p = pathlib.Path({repo!r}) / 'phase2' / 'runners' / 'run_phase2_strategy_self_optimize.py'\n"
        "spec = importlib.util.spec_from_file_location('rphsso', str(p))\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['rphsso'] = mod\n"
        "spec.loader.exec_module(mod)".format(repo=str(REPO_ROOT))
    )
    assert rc == 0, output
    assert "OK" in output
