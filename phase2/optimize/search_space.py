"""Search-space loader & proposal validator.

Loads ``phase2/strategy/config/optimize_search_space.yaml`` into a
typed in-memory representation, enforces the ``frozen`` blacklist and
the hard ceilings stated in requirements 2.1-2.5, and validates each
optimizer proposal against the space.

Stdlib + PyYAML only; no numpy / pandas / pydantic dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


# Hard rules from requirement 2.2 — these win even if a future YAML
# tries to widen them. The loader clamps the declared yaml min/max into
# these envelopes and rejects defaults / proposals that breach them.
HARD_CEILINGS: dict[str, dict[str, float]] = {
    "pool_budget_pct": {"max": 0.95},
    "max_concurrent_holdings": {"max": 8},
    "stop_loss_pct": {"min": 0.02, "max": 0.10},
}

# These attributes belong to the strategy but must never be flipped or
# widened by the optimizer. The yaml `frozen` list MUST be a superset
# of this default set; we enforce it on load.
DEFAULT_FROZEN_PARAMS: frozenset[str] = frozenset(
    {
        "LIVE_SUBMIT",
        "_pool",
        "max_orders_per_day",
    }
)


_VALID_TYPES = ("int", "float", "bool")


# ---------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str  # "int" | "float" | "bool"
    default: Any
    min: float | int | None = None
    max: float | int | None = None
    step: float | int | None = None
    choices: tuple[Any, ...] | None = None  # discrete enum (optional)

    def cast(self, value: Any) -> Any:
        if self.type == "int":
            return int(value)
        if self.type == "float":
            return float(value)
        if self.type == "bool":
            return bool(value)
        raise ValueError(f"unsupported type {self.type!r} on param {self.name}")

    def is_in_range(self, value: Any) -> bool:
        if self.choices is not None:
            return value in self.choices
        if self.type == "bool":
            return value in (True, False)
        if self.min is not None and value < self.min:
            return False
        if self.max is not None and value > self.max:
            return False
        return True


@dataclass(frozen=True)
class SearchSpace:
    version: int
    frozen: frozenset[str]
    params: dict[str, ParamSpec]
    source_path: str

    def names(self) -> list[str]:
        return list(self.params.keys())

    def defaults(self) -> dict[str, Any]:
        return {n: p.default for n, p in self.params.items()}

    def to_summary(self) -> dict[str, Any]:
        """Compact dict suitable for embedding in evaluations.json."""
        out: dict[str, Any] = {
            "version": self.version,
            "frozen": sorted(self.frozen),
            "params": {},
            "source_path": self.source_path,
        }
        for name, p in self.params.items():
            out["params"][name] = {
                "type": p.type,
                "default": p.default,
                "min": p.min,
                "max": p.max,
                "step": p.step,
                "choices": list(p.choices) if p.choices is not None else None,
            }
        return out


# ---------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------


def _coerce_param_spec(name: str, raw: Mapping[str, Any]) -> ParamSpec:
    if not isinstance(raw, Mapping):
        raise ValueError(f"param {name!r}: spec must be a mapping, got {type(raw).__name__}")
    ptype = raw.get("type")
    if ptype not in _VALID_TYPES:
        raise ValueError(
            f"param {name!r}: type must be one of {_VALID_TYPES!r}, got {ptype!r}"
        )

    if "default" not in raw:
        raise ValueError(f"param {name!r}: missing required 'default'")

    choices_raw = raw.get("choices")
    choices: tuple[Any, ...] | None = None
    if choices_raw is not None:
        if not isinstance(choices_raw, (list, tuple)) or not choices_raw:
            raise ValueError(f"param {name!r}: 'choices' must be a non-empty list")
        choices = tuple(choices_raw)

    pmin = raw.get("min")
    pmax = raw.get("max")
    pstep = raw.get("step")

    if ptype in ("int", "float"):
        if choices is None:
            if pmin is None or pmax is None:
                raise ValueError(
                    f"param {name!r}: numeric params require both 'min' and 'max'"
                )
            if pmin > pmax:
                raise ValueError(f"param {name!r}: min {pmin} > max {pmax}")
        if name in HARD_CEILINGS:
            ceiling = HARD_CEILINGS[name]
            if "max" in ceiling and pmax is not None and pmax > ceiling["max"]:
                raise ValueError(
                    f"param {name!r}: declared max {pmax} exceeds hard ceiling "
                    f"{ceiling['max']}"
                )
            if "min" in ceiling and pmin is not None and pmin < ceiling["min"]:
                raise ValueError(
                    f"param {name!r}: declared min {pmin} below hard floor "
                    f"{ceiling['min']}"
                )

    spec = ParamSpec(
        name=name,
        type=ptype,
        default=raw["default"],
        min=pmin,
        max=pmax,
        step=pstep,
        choices=choices,
    )

    # Validate default against the declared range.
    if not spec.is_in_range(spec.default):
        raise ValueError(
            f"param {name!r}: default {spec.default!r} is outside the declared range"
        )
    return spec


def load_search_space(path: str | Path) -> SearchSpace:
    """Parse the YAML file into a :class:`SearchSpace` and validate it.

    Raises :class:`ValueError` on any structural / frozen-param /
    range-ceiling violation.
    """

    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, Mapping):
        raise ValueError(f"{p}: top-level must be a mapping")

    version = int(raw.get("version", 1))

    frozen_yaml = raw.get("frozen") or []
    if not isinstance(frozen_yaml, (list, tuple)):
        raise ValueError(f"{p}: 'frozen' must be a list")
    frozen = frozenset(str(x) for x in frozen_yaml) | DEFAULT_FROZEN_PARAMS

    params_raw = raw.get("params") or {}
    if not isinstance(params_raw, Mapping):
        raise ValueError(f"{p}: 'params' must be a mapping")

    if not params_raw:
        raise ValueError(f"{p}: 'params' must declare at least one tunable parameter")

    params: dict[str, ParamSpec] = {}
    for name, spec_raw in params_raw.items():
        if name in frozen:
            raise ValueError(
                f"{p}: param {name!r} is in 'frozen' and cannot also appear "
                "under 'params'"
            )
        params[name] = _coerce_param_spec(name, spec_raw)

    return SearchSpace(version=version, frozen=frozen, params=params, source_path=str(p))


# ---------------------------------------------------------------------
# Proposal validation
# ---------------------------------------------------------------------


def validate_proposal(proposal: Mapping[str, Any], space: SearchSpace) -> dict[str, Any]:
    """Return a normalised, type-coerced copy of ``proposal``.

    Raises :class:`ValueError` if any key references a frozen param or
    falls outside its declared range. Per requirement 2.3 we *never*
    silently clamp.
    """

    if not isinstance(proposal, Mapping):
        raise ValueError(f"proposal must be a mapping, got {type(proposal).__name__}")

    normalized: dict[str, Any] = {}
    for key, value in proposal.items():
        if key in space.frozen:
            raise ValueError(
                f"proposal touches frozen param {key!r}; rejected per requirement 2.2"
            )
        if key not in space.params:
            raise ValueError(f"proposal contains unknown param {key!r}")
        spec = space.params[key]
        try:
            casted = spec.cast(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"param {key!r}: cannot cast {value!r} to {spec.type}: {exc}"
            ) from exc
        if not spec.is_in_range(casted):
            raise ValueError(
                f"param {key!r}: value {casted!r} outside the declared range "
                f"(min={spec.min}, max={spec.max}, choices={spec.choices})"
            )
        # Apply hard ceiling one more time (defence in depth).
        if key in HARD_CEILINGS:
            ceiling = HARD_CEILINGS[key]
            if "max" in ceiling and casted > ceiling["max"]:
                raise ValueError(
                    f"param {key!r}: value {casted!r} exceeds hard ceiling "
                    f"{ceiling['max']}"
                )
            if "min" in ceiling and casted < ceiling["min"]:
                raise ValueError(
                    f"param {key!r}: value {casted!r} below hard floor "
                    f"{ceiling['min']}"
                )
        normalized[key] = casted
    return normalized


def merge_with_defaults(
    proposal: Mapping[str, Any], space: SearchSpace
) -> dict[str, Any]:
    """Return ``defaults <- validated proposal`` (proposal wins).

    The result is a dict containing every safe_param in the space.
    """
    out = space.defaults()
    out.update(validate_proposal(proposal, space))
    return out


__all__ = [
    "DEFAULT_FROZEN_PARAMS",
    "HARD_CEILINGS",
    "ParamSpec",
    "SearchSpace",
    "load_search_space",
    "merge_with_defaults",
    "validate_proposal",
]
