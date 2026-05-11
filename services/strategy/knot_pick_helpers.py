"""Knot four-dimension stock-pick helpers.

This module powers three lightweight workflow entries:

* ``run_knot_4dim_picks_hk.py`` / ``run_knot_4dim_picks_us.py`` ask the
  remote Knot agent to propose three names per dimension (technical,
  fundamental, capital flow, event driven) for a given market and run them
  through the local ``CandidateScoringService``.
* ``run_holdings_knot_review.py`` reads the current Futu account holdings
  (read-only, no orders submitted), scores them with the same local model
  and asks Knot for one of four directional actions per name. All raw
  cash, quantity and account-id fields are masked before the prompt is
  sent to Knot and before anything is written to disk or printed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from vnpy_llm.llm_client import LlmClientError, OpenAICompatibleClient

from .candidate_scoring import CandidateScoringService
from .symbols import normalize_symbol

SUPPORTED_MARKETS = ("hong_kong", "us")

# (key, label, signal_category)
DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("technical", "Technical", "technical"),
    ("fundamental", "Fundamental", "fundamental"),
    ("capital_flow", "Capital Flow", "flow"),
    ("event_driven", "Event Driven", "event"),
)

DEFAULT_PER_DIM = 3

HOLDING_ACTIONS = ("hold", "add", "trim", "exit")

# Sensitive numeric fields we never propagate to logs / prompts / output files.
SENSITIVE_NUMERIC_FIELDS = (
    "qty",
    "market_val",
    "cost",
    "cost_price",
    "cash",
    "buying_power",
    "total_assets",
    "nominal_price",
    "available_qty",
    "frozen_qty",
)

# Position weight thresholds (relative to total assets):
#   ratio <  5%        -> small
#   5%  <= ratio < 15% -> medium
#   ratio >= 15%       -> large
WEIGHT_BUCKETS: tuple[tuple[float, str], ...] = ((0.05, "small"), (0.15, "medium"))


class KnotPickError(RuntimeError):
    """Raised when the remote Knot agent is unavailable or returned junk."""


@dataclass
class FourDimPicksResult:
    market: str
    generated_at: str
    raw_response: str
    dimensions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def total_picks(self) -> int:
        return sum(len(rows) for rows in self.dimensions.values())


@dataclass
class HoldingsReviewResult:
    generated_at: str
    env: str
    position_count: int
    rows: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Knot client construction (mirrors KnotCandidateSeedService._build_client)
# ---------------------------------------------------------------------------
def _default_client_factory() -> OpenAICompatibleClient | None:
    base_url = os.environ.get("KNOT_AGUI_URL") or os.environ.get("KNOT_BASE_URL") or ""
    api_key = os.environ.get("KNOT_API_TOKEN") or ""
    if not base_url or not api_key:
        return None
    try:
        return OpenAICompatibleClient(
            base_url=base_url,
            api_key=api_key,
            model=os.environ.get("KNOT_MODEL", ""),
            api_type="knot_agui",
            api_user=os.environ.get("KNOT_API_USER", ""),
            enable_web_search=os.environ.get("KNOT_ENABLE_WEB_SEARCH") == "YES",
            timeout_seconds=int(os.environ.get("KNOT_TIMEOUT_SECONDS", "60")),
            conversation_id=os.environ.get("KNOT_CONVERSATION_ID", ""),
        )
    except Exception:  # pragma: no cover
        return None


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return round(default, 4)
    if score != score:  # NaN guard
        return round(default, 4)
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# Four-dimension picks
# ---------------------------------------------------------------------------
def _build_four_dim_prompt(*, market: str, per_dim: int) -> str:
    market_label = "Hong Kong" if market == "hong_kong" else "United States"
    market_value = "hong_kong" if market == "hong_kong" else "us"
    suffix_hint = "00700.HK" if market == "hong_kong" else "NVDA.US"
    dims = ", ".join(key for key, _, _ in DIMENSIONS)
    return (
        "You are an institutional research assistant. Propose follow-up "
        f"candidates for the {market_label} equity market across four "
        f"research dimensions ({dims}).\n"
        "Return strictly valid JSON with exactly these keys: "
        "{\"technical\": [...], \"fundamental\": [...], "
        "\"capital_flow\": [...], \"event_driven\": [...]}.\n"
        f"Each list MUST contain exactly {per_dim} candidate objects.\n"
        "Each candidate object must contain these fields:\n"
        f"  symbol: ticker in dotted form (e.g. {suffix_hint})\n"
        f"  market: '{market_value}'\n"
        "  name: human-readable company name\n"
        "  raw_score: float in [0, 1] reflecting your conviction\n"
        "  rationale: short English sentence explaining why it fits THIS dimension\n"
        "  risk: short English sentence describing the dominant near-term risk\n"
        "  action_hint: short English sentence describing the suggested follow-up\n"
        "Constraints:\n"
        f"- Only return symbols that actually trade in the {market_label} market.\n"
        "- Avoid penny stocks, illiquid tickers, halted names and OTC-only listings.\n"
        "- Pick names that differ across dimensions when possible.\n"
        "- Do not include any commentary, markdown, or text outside the JSON object."
    )


def _parse_dimension_rows(
    raw: Any,
    *,
    market: str,
    dim_key: str,
    signal_category: str,
    per_dim: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return []
    items = raw.get(dim_key)
    if not isinstance(items, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        raw_symbol = str(item.get("symbol") or "").strip()
        # First reject explicit cross-market suffixes BEFORE normalize_symbol
        # rewrites them: e.g. ``NVDA.US`` must not survive a ``hong_kong``
        # request even though normalize would zero-pad it into ``0NVDA.HK``.
        if "." in raw_symbol:
            raw_suffix = raw_symbol.rsplit(".", 1)[-1].upper()
            if market == "hong_kong" and raw_suffix not in {"", "HK"}:
                continue
            if market == "us" and raw_suffix not in {"", "US"}:
                continue
        symbol = normalize_symbol(raw_symbol, market)
        if not symbol or "." not in symbol:
            continue
        suffix = symbol.rsplit(".", 1)[-1]
        if market == "hong_kong" and suffix != "HK":
            continue
        if market == "us" and suffix != "US":
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        score = _coerce_float(item.get("raw_score"), default=0.55)
        rationale = str(item.get("rationale") or "").strip()
        rows.append(
            {
                "symbol": symbol,
                "market": market,
                "candidate_type": "dynamic",
                "name": str(item.get("name") or symbol).strip() or symbol,
                "raw_score": score,
                "rationale": rationale,
                "risk": str(item.get("risk") or "").strip(),
                "action_hint": str(item.get("action_hint") or "").strip(),
                "dimension": dim_key,
                "confidence_source": "knot_4dim_pick",
                "signals": [
                    {
                        "symbol": symbol,
                        "market": market,
                        "source": "knot_4dim_pick",
                        "category": signal_category,
                        "score": score,
                        "summary": rationale or f"knot {dim_key} pick",
                    }
                ],
            }
        )
        if len(rows) >= per_dim:
            break
    return rows


def call_knot_4dim_picks(
    *,
    market: str,
    per_dim: int = DEFAULT_PER_DIM,
    client_factory: Any | None = None,
) -> FourDimPicksResult:
    """Call Knot once and return the four-dimension picks for ``market``."""

    market_key = (market or "").strip().lower()
    if market_key not in SUPPORTED_MARKETS:
        raise KnotPickError(f"Unsupported market: {market}")
    bounded_per_dim = max(1, min(int(per_dim or 0), 10))

    factory = client_factory or _default_client_factory
    client = factory()
    if client is None:
        raise KnotPickError(
            "Knot client is not configured. Set KNOT_AGUI_URL / KNOT_API_TOKEN "
            "(and optionally KNOT_API_USER) before running this entry."
        )

    prompt = _build_four_dim_prompt(market=market_key, per_dim=bounded_per_dim)
    try:
        raw = client.complete_json("", prompt)
    except (LlmClientError, OSError, ValueError) as exc:
        raise KnotPickError(f"Knot call failed: {exc}") from exc

    dims: dict[str, list[dict[str, Any]]] = {}
    for dim_key, _label, signal_category in DIMENSIONS:
        dims[dim_key] = _parse_dimension_rows(
            raw,
            market=market_key,
            dim_key=dim_key,
            signal_category=signal_category,
            per_dim=bounded_per_dim,
        )

    if all(not rows for rows in dims.values()):
        raise KnotPickError(
            "Knot response did not contain any usable candidates across the four dimensions."
        )

    raw_text = json.dumps(raw, ensure_ascii=False) if isinstance(raw, (dict, list)) else str(raw or "")
    return FourDimPicksResult(
        market=market_key,
        generated_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        raw_response=raw_text,
        dimensions=dims,
    )


# ---------------------------------------------------------------------------
# Local scoring + compact text rendering
# ---------------------------------------------------------------------------
def score_candidate_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    mode: str = "dynamic",
    service: CandidateScoringService | None = None,
    generated_at: str | None = None,
) -> list[dict[str, Any]]:
    """Run each row through ``CandidateScoringService.enrich_row``."""

    scoring = service or CandidateScoringService()
    enriched: list[dict[str, Any]] = []
    ts = generated_at or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in rows:
        try:
            enriched_row = scoring.enrich_row(dict(row), mode=mode, generated_at=ts)
        except Exception as exc:  # pragma: no cover
            enriched_row = dict(row)
            enriched_row["scoring_error"] = f"{exc.__class__.__name__}: {exc}"
        enriched.append(enriched_row)
    return enriched


def format_four_dim_picks(
    picks: FourDimPicksResult,
    *,
    enriched: Mapping[str, Sequence[Mapping[str, Any]]],
) -> str:
    market_label = "HK" if picks.market == "hong_kong" else "US"
    lines: list[str] = []
    lines.append(f"[{market_label} 4-Dim Picks @ {picks.generated_at}]")
    total = 0
    for dim_key, label, _category in DIMENSIONS:
        rows = list(enriched.get(dim_key) or [])
        total += len(rows)
        lines.append(f"-- {label} " + "-" * max(1, 40 - len(label)))
        if not rows:
            lines.append("    (no usable picks)")
            continue
        for idx, row in enumerate(rows, start=1):
            symbol = str(row.get("symbol") or "").strip()
            name = str(row.get("name") or symbol).strip()
            raw_score = _coerce_float(row.get("raw_score"), default=0.0)
            trend = _coerce_float(row.get("trend_score"), default=0.0)
            flow = _coerce_float(row.get("flow_score"), default=0.0)
            event = _coerce_float(row.get("event_score"), default=0.0)
            quality = _coerce_float(row.get("quality_score"), default=0.0)
            risk_level = str(row.get("risk_level") or "?").strip() or "?"
            rationale = (str(row.get("rationale") or "").strip() or "n/a")[:80]
            risk = (str(row.get("risk") or "").strip() or "n/a")[:80]
            action = (str(row.get("action_hint") or "").strip() or "n/a")[:80]
            lines.append(
                f"  {idx}. {symbol:<10} {name[:18]:<18} score={raw_score:.2f}  "
                f"trend={trend:.2f} flow={flow:.2f} event={event:.2f} quality={quality:.2f}  risk={risk_level}"
            )
            lines.append(f"      rationale: {rationale}")
            lines.append(f"      risk:      {risk}")
            lines.append(f"      action:    {action}")
    lines.append(f"summary: dims={len(DIMENSIONS)} picks={total} knot=ok scoring=dynamic_hybrid_v3")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Holdings review (account read + Knot directional advice)
# ---------------------------------------------------------------------------
def classify_weight(market_val: float | None, total_assets: float | None) -> str:
    if not market_val or not total_assets or float(total_assets) <= 0:
        return "unknown"
    ratio = float(market_val) / float(total_assets)
    if ratio <= 0:
        return "unknown"
    for upper, label in WEIGHT_BUCKETS:
        if ratio < upper:
            return label
    return "large"


def classify_pl_direction(pl_ratio: float | None) -> str:
    if pl_ratio is None:
        return "flat"
    try:
        value = float(pl_ratio)
    except (TypeError, ValueError):
        return "flat"
    if value > 0.005:
        return "up"
    if value < -0.005:
        return "down"
    return "flat"


def _market_from_code(code: str) -> str:
    text = (code or "").strip().upper()
    if text.startswith("HK"):
        return "hong_kong"
    if text.startswith("US"):
        return "us"
    if text.endswith(".HK"):
        return "hong_kong"
    if text.endswith(".US"):
        return "us"
    return ""


def _normalize_position_symbol(code: str, market: str) -> str:
    text = (code or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        head, tail = text.split(".", 1)
        if head in {"HK", "US"} and tail:
            return f"{tail}.{head}"
        return text
    if market == "hong_kong":
        return f"{text}.HK"
    if market == "us":
        return f"{text}.US"
    return text


def mask_position_record(
    *,
    code: str,
    name: str,
    market: str,
    weight_bucket: str,
    pl_direction: str,
) -> dict[str, Any]:
    return {
        "symbol": _normalize_position_symbol(code, market),
        "raw_code": str(code or "").upper(),
        "name": str(name or "").strip() or _normalize_position_symbol(code, market),
        "market": market,
        "weight_bucket": weight_bucket,
        "pl_direction": pl_direction,
    }


def mask_account_summary(summary: Any) -> dict[str, Any]:
    """Return a compact, scrubbed view of ``FutuAccountSummary``.

    The result intentionally excludes every cash / market-value / quantity
    field; only env, position count and masked position records survive.
    """

    if summary is None:
        return {"env": "?", "position_count": 0, "positions": []}
    positions = list(getattr(summary, "positions", []) or [])
    total_assets = getattr(summary, "total_assets", None)
    masked: list[dict[str, Any]] = []
    for pos in positions:
        code = getattr(pos, "code", "")
        market = _market_from_code(code)
        masked.append(
            mask_position_record(
                code=code,
                name=getattr(pos, "name", ""),
                market=market,
                weight_bucket=classify_weight(getattr(pos, "market_val", None), total_assets),
                pl_direction=classify_pl_direction(getattr(pos, "pl_ratio", None)),
            )
        )
    return {
        "env": str(getattr(summary, "env", "?") or "?"),
        "position_count": len(masked),
        "positions": masked,
    }


def build_holdings_rows(masked_positions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pos in masked_positions:
        symbol = str(pos.get("symbol") or "").strip()
        market = str(pos.get("market") or "").strip()
        if not symbol or not market:
            continue
        rows.append(
            {
                "symbol": symbol,
                "market": market,
                "candidate_type": "holding",
                "name": str(pos.get("name") or symbol).strip() or symbol,
                "rationale": "Existing holding under review.",
                "risk": "Holding-level review only; sizing details masked.",
                "action_hint": "Refer to Knot directional advice below.",
                "signals": [
                    {
                        "symbol": symbol,
                        "market": market,
                        "source": "futu_account_holding",
                        "category": "research",
                        "score": 0.55,
                        "summary": "current portfolio holding",
                    }
                ],
            }
        )
    return rows


def _build_holdings_prompt(masked_positions: Sequence[Mapping[str, Any]]) -> str:
    bullet_lines = []
    for pos in masked_positions:
        bullet_lines.append(
            f"- symbol={pos.get('symbol')} name={pos.get('name')} "
            f"market={pos.get('market')} weight_bucket={pos.get('weight_bucket')} "
            f"pl_direction={pos.get('pl_direction')}"
        )
    bullets = "\n".join(bullet_lines) if bullet_lines else "(no positions)"
    actions = "|".join(HOLDING_ACTIONS)
    return (
        "You are an institutional portfolio assistant. The user shares a list of "
        "current holdings. CASH AMOUNTS, QUANTITIES AND ACCOUNT IDENTIFIERS HAVE "
        "BEEN MASKED ON PURPOSE -- never ask for them and never invent numbers.\n"
        "Holdings:\n"
        f"{bullets}\n"
        "Return strictly valid JSON: {\"items\": [<advice>, ...]}.\n"
        "Each advice object must contain these fields:\n"
        "  symbol: dotted form, must match one of the inputs above\n"
        f"  action: one of {actions}\n"
        "  confidence: float in [0, 1]\n"
        "  reason: short English sentence (<= 60 chars), directional only\n"
        "  risk_flags: list of short English tags (may be empty)\n"
        "Hard rules:\n"
        "- DO NOT include any specific share count, dollar amount, or percent of NAV.\n"
        "- DO NOT recommend leverage, options, or derivative trades.\n"
        "- Stick to the four directional actions above; we will translate them locally."
    )


def call_knot_holdings_advice(
    masked_positions: Sequence[Mapping[str, Any]],
    *,
    client_factory: Any | None = None,
) -> dict[str, dict[str, Any]]:
    if not masked_positions:
        return {}
    factory = client_factory or _default_client_factory
    client = factory()
    if client is None:
        raise KnotPickError(
            "Knot client is not configured. Set KNOT_AGUI_URL / KNOT_API_TOKEN "
            "(and optionally KNOT_API_USER) before running this entry."
        )
    prompt = _build_holdings_prompt(masked_positions)
    try:
        raw = client.complete_json("", prompt)
    except (LlmClientError, OSError, ValueError) as exc:
        raise KnotPickError(f"Knot call failed: {exc}") from exc

    items: Iterable[Any]
    if isinstance(raw, Mapping) and isinstance(raw.get("items"), list):
        items = raw["items"]
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    by_symbol: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        action = str(item.get("action") or "").strip().lower()
        if not symbol or action not in HOLDING_ACTIONS:
            continue
        by_symbol[symbol] = {
            "action": action,
            "confidence": _coerce_float(item.get("confidence"), default=0.5),
            "reason": str(item.get("reason") or "").strip()[:120],
            "risk_flags": [
                str(flag).strip()
                for flag in (item.get("risk_flags") or [])
                if str(flag).strip()
            ],
        }
    if not by_symbol:
        raise KnotPickError("Knot response did not contain any usable holdings advice.")
    return by_symbol


def merge_holdings_advice(
    masked_positions: Sequence[Mapping[str, Any]],
    enriched_rows: Sequence[Mapping[str, Any]],
    advice_by_symbol: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    enriched_by_symbol = {
        str(row.get("symbol") or "").upper(): row for row in enriched_rows
    }
    out: list[dict[str, Any]] = []
    for pos in masked_positions:
        symbol = str(pos.get("symbol") or "").upper()
        enriched = enriched_by_symbol.get(symbol, {})
        advice = advice_by_symbol.get(symbol, {})
        out.append(
            {
                "symbol": pos.get("symbol"),
                "name": pos.get("name"),
                "market": pos.get("market"),
                "weight_bucket": pos.get("weight_bucket"),
                "pl_direction": pos.get("pl_direction"),
                "raw_score": _coerce_float(enriched.get("raw_score"), default=0.0),
                "trend_score": _coerce_float(enriched.get("trend_score"), default=0.0),
                "flow_score": _coerce_float(enriched.get("flow_score"), default=0.0),
                "event_score": _coerce_float(enriched.get("event_score"), default=0.0),
                "quality_score": _coerce_float(enriched.get("quality_score"), default=0.0),
                "risk_level": str(enriched.get("risk_level") or "?"),
                "knot_action": str(advice.get("action") or "review"),
                "knot_confidence": _coerce_float(advice.get("confidence"), default=0.0),
                "knot_reason": str(advice.get("reason") or "")[:120],
                "knot_risk_flags": list(advice.get("risk_flags") or []),
            }
        )
    return out


def format_holdings_review(result: HoldingsReviewResult) -> str:
    lines: list[str] = []
    lines.append(
        f"[Holdings Review @ {result.generated_at}] env={result.env} "
        f"position_count={result.position_count} (amounts/quantities masked)"
    )
    if not result.rows:
        lines.append("  (no positions)")
        return "\n".join(lines)
    for idx, row in enumerate(result.rows, start=1):
        symbol = str(row.get("symbol") or "").strip()
        name = str(row.get("name") or symbol)[:18]
        weight = str(row.get("weight_bucket") or "?")
        pl = str(row.get("pl_direction") or "?")
        score = _coerce_float(row.get("raw_score"), default=0.0)
        action = str(row.get("knot_action") or "review")
        reason = str(row.get("knot_reason") or "n/a")[:60]
        flags = ",".join(row.get("knot_risk_flags") or []) or "-"
        lines.append(
            f"  {idx}. {symbol:<10} {name:<18} weight={weight:<6} pl={pl:<4} "
            f"score={score:.2f} action={action:<5} conf={_coerce_float(row.get('knot_confidence'), default=0.0):.2f}"
        )
        lines.append(f"      reason: {reason}")
        lines.append(f"      risk_flags: {flags}")
    return "\n".join(lines)


__all__ = [
    "DIMENSIONS",
    "DEFAULT_PER_DIM",
    "FourDimPicksResult",
    "HOLDING_ACTIONS",
    "HoldingsReviewResult",
    "KnotPickError",
    "SUPPORTED_MARKETS",
    "WEIGHT_BUCKETS",
    "SENSITIVE_NUMERIC_FIELDS",
    "build_holdings_rows",
    "call_knot_4dim_picks",
    "call_knot_holdings_advice",
    "classify_pl_direction",
    "classify_weight",
    "format_four_dim_picks",
    "format_holdings_review",
    "mask_account_summary",
    "mask_position_record",
    "merge_holdings_advice",
    "score_candidate_rows",
]
