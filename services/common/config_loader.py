from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


def load_yaml(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML config must be a mapping: {p}")
    return data


def load_yaml_limits_block(path: str | Path) -> Dict[str, Any]:
    """Read the ``limits:`` mapping from a risk YAML file.

    Used by both ``scripts/classic_multifactor/_base_runner.py`` and
    ``services/trading_pipeline/live_task.py`` to pull the canonical risk
    limits from ``configs/risk/live_risk_limits.yaml``. Values that look
    like integers (``1.0`` / ``100``) are normalised to ``int``; everything
    else is preserved as parsed by PyYAML.

    Returns an empty dict if the file does not exist or has no ``limits:``
    block — both fallbacks are intentional so callers can layer in CLI /
    in-memory overrides without first checking for the file.
    """
    p = Path(path)
    if not p.exists():
        return {}
    data = load_yaml(p)
    block = data.get("limits") or {}
    if not isinstance(block, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in block.items():
        if isinstance(value, float) and value.is_integer():
            out[str(key)] = int(value)
        else:
            out[str(key)] = value
    return out
