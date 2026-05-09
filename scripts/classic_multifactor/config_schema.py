from __future__ import annotations

"""Single-source-of-truth schema for classic_multifactor configuration.

Historically ``ClassicMultiFactorConfig`` (see ``model.py``) was duplicated in:

* ``us_single_symbol_multifactor.py`` argparse definitions
* ``run_vnpy_cta_*.py`` series
* ``strategy.py`` CTA Template classvars
* ``run.py`` ``minute_defaults`` block
* ``flow.py`` and ``backtest.py`` ``MinuteTradeGuardConfig(...)`` constructors

This module centralises the cross-cutting bits so each entry point only wires
``add_config_args`` + ``from_args`` / ``from_setting``; the underlying dataclass
``ClassicMultiFactorConfig`` remains the single authoritative field list.

It also adds runtime-only fields that are *not* part of the model dataclass but
share the same lifecycle (minute guard + account-level risk knobs) and are
passed through the same JSON config / CLI surface.
"""

import argparse
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.classic_multifactor.minute_guard import MinuteTradeGuardConfig
from scripts.classic_multifactor.model import ClassicMultiFactorConfig


# ---------------------------------------------------------------------------
# MinuteTradeGuardConfig factory
# ---------------------------------------------------------------------------

def minute_guard_from_setting(setting: Mapping[str, Any] | None) -> MinuteTradeGuardConfig:
    """Build a ``MinuteTradeGuardConfig`` from a flat setting dict.

    Unknown keys are ignored. Used by ``backtest.py`` / ``strategy.py`` /
    ``flow.py`` / ``live_task.py`` to avoid four copies of the same mapping.
    """
    data = dict(setting or {})
    return MinuteTradeGuardConfig(
        max_intraday_trades=int(data.get("max_intraday_trades", 0) or 0),
        entry_cooldown_minutes=int(data.get("entry_cooldown_minutes", 0) or 0),
        min_hold_minutes=int(data.get("min_hold_minutes", 0) or 0),
        no_new_entry_after=str(data.get("no_new_entry_after", "") or ""),
    )


# ---------------------------------------------------------------------------
# Full classic multifactor setting schema
# ---------------------------------------------------------------------------

# Runtime / execution-level knobs shared across entry points but not part of
# the pure ``ClassicMultiFactorModel`` decision dataclass.
_RUNTIME_FIELD_DEFAULTS: dict[str, Any] = {
    # Minute guard (also consumed by MinuteTradeGuardConfig)
    "max_intraday_trades": 0,
    "entry_cooldown_minutes": 0,
    "min_hold_minutes": 0,
    "no_new_entry_after": "",
    # Regime filter (CTA strategy gating)
    "regime_filter_mode": "off",
    "regime_trend_lookback": 5,
    "regime_ema_span": 20,
    "regime_atr_pct_lo": 0.008,
    "regime_atr_pct_hi": 0.05,
    "regime_atr_days": 5,
}


def _model_field_defaults() -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for f in fields(ClassicMultiFactorConfig):
        defaults[f.name] = f.default
    return defaults


def full_setting_defaults() -> dict[str, Any]:
    """Return the *canonical* default setting dict (model + runtime)."""
    combined: dict[str, Any] = {}
    combined.update(_model_field_defaults())
    combined.update(_RUNTIME_FIELD_DEFAULTS)
    return combined


# Conservative minute-level profile (extracted from the legacy
# ``run.py`` ``minute_defaults`` block). Applied via ``setdefault`` only,
# never overwriting JSON config values.
MINUTE_PROFILE_SETTING: dict[str, Any] = {
    "fast_window": 6,
    "slow_window": 24,
    "momentum_window": 12,
    "atr_window": 14,
    "signal_interval_minutes": 5,
    "confirm_bars": 1,
    "entry_score": 0.66,
    "min_volume_ratio": 0.8,
    "min_atr_pct": 0.0012,
    "min_trend_score": 0.55,
    "max_intraday_trades": 4,
    "entry_cooldown_minutes": 30,
    "min_hold_minutes": 20,
    "no_new_entry_after": "15:30",
    "stop_atr": 1.5,
    "take_profit_atr": 2.5,
    "trailing_atr": 2.0,
}


def apply_minute_profile(setting: dict[str, Any]) -> dict[str, Any]:
    """Apply the conservative minute profile as *setdefault* only.

    JSON-supplied values win; this only fills holes.
    """
    for k, v in MINUTE_PROFILE_SETTING.items():
        setting.setdefault(k, v)
    return setting


# ---------------------------------------------------------------------------
# Argparse integration
# ---------------------------------------------------------------------------

_ARG_NAME_OVERRIDES: dict[str, str] = {}


def _to_flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def _field_type(value: Any) -> Any:
    if isinstance(value, bool):  # argparse bool handled separately
        return bool
    if isinstance(value, int):
        return int
    if isinstance(value, float):
        return float
    return str


def _iter_config_fields() -> Iterable[tuple[str, Any]]:
    for f in fields(ClassicMultiFactorConfig):
        yield f.name, f.default
    for k, v in _RUNTIME_FIELD_DEFAULTS.items():
        yield k, v


def add_config_args(parser: argparse.ArgumentParser, *, include_runtime: bool = True) -> None:
    """Attach config flags to a parser.

    Only fields declared in ``ClassicMultiFactorConfig`` (+ optionally the
    runtime block) are exposed. Defaults come from the dataclass itself, so
    bumping a default in ``model.py`` automatically propagates to every CLI.
    """
    seen: set[str] = set()
    for f in fields(ClassicMultiFactorConfig):
        seen.add(f.name)
        ftype = _field_type(f.default)
        parser.add_argument(_to_flag(f.name), type=ftype, default=f.default, dest=f.name)
    if include_runtime:
        for k, v in _RUNTIME_FIELD_DEFAULTS.items():
            if k in seen:
                continue
            ftype = _field_type(v)
            parser.add_argument(_to_flag(k), type=ftype, default=v, dest=k)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def model_config_from_setting(setting: Mapping[str, Any] | None) -> ClassicMultiFactorConfig:
    """Build the pure model ``ClassicMultiFactorConfig`` from a flat dict.

    Unknown keys (e.g. minute-guard / regime filter) are silently dropped here;
    those are handled by other factories.
    """
    data = dict(setting or {})
    allowed = {f.name for f in fields(ClassicMultiFactorConfig)}
    kwargs: dict[str, Any] = {}
    for f in fields(ClassicMultiFactorConfig):
        if f.name not in data:
            continue
        raw = data[f.name]
        if raw is None:
            continue
        try:
            if isinstance(f.default, bool):
                kwargs[f.name] = bool(raw)
            elif isinstance(f.default, int) and not isinstance(f.default, bool):
                kwargs[f.name] = int(raw)
            elif isinstance(f.default, float):
                kwargs[f.name] = float(raw)
            else:
                kwargs[f.name] = raw
        except (TypeError, ValueError):
            kwargs[f.name] = f.default
    return ClassicMultiFactorConfig(**kwargs)


def model_config_from_args(args: argparse.Namespace) -> ClassicMultiFactorConfig:
    """Build ``ClassicMultiFactorConfig`` from argparse namespace."""
    payload = {f.name: getattr(args, f.name) for f in fields(ClassicMultiFactorConfig) if hasattr(args, f.name)}
    return model_config_from_setting(payload)


def setting_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Serialise argparse namespace into a canonical setting dict."""
    out: dict[str, Any] = {}
    for name, _default in _iter_config_fields():
        if hasattr(args, name):
            out[name] = getattr(args, name)
    return out


def setting_from_json(path: str | Path) -> dict[str, Any]:
    """Load a ``configs/classic_multifactor/*.json`` and extract setting.

    Accepts either ``{"setting": {...}}`` or a flat object.
    """
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "setting" in raw and isinstance(raw["setting"], dict):
        return dict(raw["setting"])
    if isinstance(raw, dict):
        return dict(raw)
    raise ValueError(f"unsupported config shape in {p}")


def setting_to_model_and_guard(setting: Mapping[str, Any]) -> tuple[ClassicMultiFactorConfig, MinuteTradeGuardConfig]:
    """Shortcut used by runtime pipelines."""
    return model_config_from_setting(setting), minute_guard_from_setting(setting)


# ---------------------------------------------------------------------------
# Unified ``ClassicMultiFactorConfig``-shaped wrapper (model + runtime)
# ---------------------------------------------------------------------------

@dataclass
class ClassicMultiFactorRuntimeConfig:
    """Aggregated config bundle covering model + minute guard + regime.

    This is intentionally *not* a frozen dataclass because CLIs may mutate it
    (e.g. ``apply_minute_profile``). Use ``to_setting`` to serialise.
    """
    model: ClassicMultiFactorConfig = field(default_factory=ClassicMultiFactorConfig)
    minute_guard: MinuteTradeGuardConfig = field(default_factory=MinuteTradeGuardConfig)
    regime: dict[str, Any] = field(default_factory=lambda: {
        k: _RUNTIME_FIELD_DEFAULTS[k]
        for k in (
            "regime_filter_mode",
            "regime_trend_lookback",
            "regime_ema_span",
            "regime_atr_pct_lo",
            "regime_atr_pct_hi",
            "regime_atr_days",
        )
    })

    # ---- factories ----
    @classmethod
    def from_setting(cls, setting: Mapping[str, Any]) -> "ClassicMultiFactorRuntimeConfig":
        data = dict(setting or {})
        regime_keys = {
            "regime_filter_mode",
            "regime_trend_lookback",
            "regime_ema_span",
            "regime_atr_pct_lo",
            "regime_atr_pct_hi",
            "regime_atr_days",
        }
        regime = {k: data[k] for k in regime_keys if k in data}
        base_regime = {
            k: _RUNTIME_FIELD_DEFAULTS[k] for k in regime_keys
        }
        base_regime.update(regime)
        return cls(
            model=model_config_from_setting(data),
            minute_guard=minute_guard_from_setting(data),
            regime=base_regime,
        )

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ClassicMultiFactorRuntimeConfig":
        return cls.from_setting(setting_from_args(args))

    @classmethod
    def from_json(cls, path: str | Path) -> "ClassicMultiFactorRuntimeConfig":
        return cls.from_setting(setting_from_json(path))

    # ---- serialisation ----
    def to_setting(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        out.update(asdict(self.model))
        out.update({
            "max_intraday_trades": self.minute_guard.max_intraday_trades,
            "entry_cooldown_minutes": self.minute_guard.entry_cooldown_minutes,
            "min_hold_minutes": self.minute_guard.min_hold_minutes,
            "no_new_entry_after": self.minute_guard.no_new_entry_after,
        })
        out.update(self.regime)
        return out


__all__ = [
    "MINUTE_PROFILE_SETTING",
    "ClassicMultiFactorRuntimeConfig",
    "add_config_args",
    "apply_minute_profile",
    "full_setting_defaults",
    "minute_guard_from_setting",
    "model_config_from_args",
    "model_config_from_setting",
    "setting_from_args",
    "setting_from_json",
    "setting_to_model_and_guard",
    "INTRADAY_ONLY_FIELDS",
    "DAILY_ONLY_FIELDS",
    "LoopModeValidationError",
    "validate_loop_mode",
    "resolve_loop_mode",
]


# ---------------------------------------------------------------------------
# loop_mode routing (intraday / daily)
# ---------------------------------------------------------------------------

# Intraday-only knobs. When a config is tagged ``loop_mode=daily`` any of
# these present with a *non-default / non-empty* value triggers a schema
# rejection to keep the two runners from sharing semantics.
INTRADAY_ONLY_FIELDS: frozenset[str] = frozenset({
    "max_intraday_trades",
    "entry_cooldown_minutes",
    "min_hold_minutes",
    "no_new_entry_after",
    "signal_interval_minutes",
})

# Daily-only knobs. These are not part of ``ClassicMultiFactorConfig`` and
# are read purely by ``scripts/classic_multifactor/run_daily_rebalance.py``.
# Presence on an ``intraday`` config is a schema error.
DAILY_ONLY_FIELDS: frozenset[str] = frozenset({
    "rebalance_time",
    "max_daily_turnover",
    "target_positions",
    "daily_new_pct_limit",
})


class LoopModeValidationError(ValueError):
    """Raised when a config mixes intraday/daily-only fields or mismatches the runner."""


def resolve_loop_mode(payload: Mapping[str, Any] | None) -> str:
    """Return the canonical loop_mode from a full config payload.

    Accepts either the top-level JSON object (``{"loop_mode": "...",
    "setting": {...}}``) or a flat setting dict. Defaults to ``"intraday"``
    when absent — preserves backwards compatibility with legacy NVDA_G09
    configs which predate the field.
    """
    if not payload:
        return "intraday"
    data = dict(payload)
    value = data.get("loop_mode")
    if value is None and isinstance(data.get("setting"), Mapping):
        value = data["setting"].get("loop_mode")
    if not value:
        return "intraday"
    text = str(value).strip().lower()
    if text not in ("intraday", "daily"):
        raise LoopModeValidationError(
            f"loop_mode must be 'intraday' or 'daily', got {value!r}"
        )
    return text


def _field_is_set(setting: Mapping[str, Any], key: str) -> bool:
    if key not in setting:
        return False
    value = setting[key]
    if value is None:
        return False
    if isinstance(value, (int, float)) and value == 0:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return False
    return True


def validate_loop_mode(
    payload: Mapping[str, Any],
    expected_mode: str,
    *,
    config_path: str | Path | None = None,
) -> str:
    """Validate ``payload`` against ``expected_mode`` ('intraday' | 'daily').

    Raises :class:`LoopModeValidationError` when:

    * ``loop_mode`` is present but does not match ``expected_mode``;
    * the payload carries non-empty fields belonging to the *opposite* mode.

    Returns the resolved ``loop_mode`` string on success.
    """
    if expected_mode not in ("intraday", "daily"):
        raise LoopModeValidationError(
            f"expected_mode must be 'intraday' or 'daily', got {expected_mode!r}"
        )

    resolved = resolve_loop_mode(payload)
    if resolved != expected_mode:
        raise LoopModeValidationError(
            f"loop_mode mismatch: runner expects {expected_mode!r} but config "
            f"declares {resolved!r} ({config_path or '<inline>'})"
        )

    # Flatten the setting for field-presence check.
    data = dict(payload)
    if isinstance(data.get("setting"), Mapping):
        flat: dict[str, Any] = dict(data["setting"])
    else:
        flat = dict(data)
    # Also consider top-level daily fields (they're allowed at top-level).
    for k in DAILY_ONLY_FIELDS:
        if k in data and k not in flat:
            flat[k] = data[k]

    if expected_mode == "daily":
        leaked = [k for k in INTRADAY_ONLY_FIELDS if _field_is_set(flat, k)]
        if leaked:
            raise LoopModeValidationError(
                f"daily config must not set intraday-only fields: {sorted(leaked)} "
                f"({config_path or '<inline>'})"
            )
    else:  # intraday
        leaked = [k for k in DAILY_ONLY_FIELDS if _field_is_set(flat, k)]
        if leaked:
            raise LoopModeValidationError(
                f"intraday config must not set daily-only fields: {sorted(leaked)} "
                f"({config_path or '<inline>'})"
            )

    return resolved
