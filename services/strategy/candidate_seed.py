from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from vnpy_llm.llm_client import LlmClientError, OpenAICompatibleClient

from .symbols import normalize_symbol

# Maximum candidates we ever ask Knot for in a single market. Acts as a guard
# rail to keep the prompt small even if the caller passes a very large value.
MAX_KNOT_TARGET_COUNT = 50

# Markets supported by the Knot dynamic candidate generation prompt.
SUPPORTED_MARKETS = {"hong_kong", "us"}


@dataclass
class KnotCandidateSeedResult:
    """Carrier for the seed-stage outcome.

    ``rows`` already conforms to the candidate input schema's required text
    fields (``symbol``, ``market``, ``name``, ``raw_score``, ``rationale``,
    ``risk``, ``action_hint``). When the upstream Knot agent is unreachable
    or returns nothing usable the caller is expected to fall back to the
    score-first universe path.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    runtime_used: str = "knot_agent_dynamic"
    raw_response: str = ""
    available: bool = True
    fallback_reason: str = ""

    def is_empty(self) -> bool:
        return not self.rows


class KnotCandidateSeedService:
    """Dynamic candidate generation via the remote Knot agent.

    This service implements the *knot-first* branch of the prepare workflow:
    we ask the remote Knot agent to propose ``target_count`` candidate symbols
    for the given market and translate the response into normalized rows that
    downstream scoring/enrichment can consume directly.

    The service never raises on transport failures. If the Knot endpoint is
    unconfigured, the network call fails, or the response cannot be parsed,
    it returns an empty :class:`KnotCandidateSeedResult` carrying the failure
    reason so the caller can downgrade to the score-first path.
    """

    runtime_id = "knot_agent_dynamic"

    def __init__(
        self,
        *,
        llm_client_factory: Any | None = None,
    ) -> None:
        # ``llm_client_factory`` is injected during unit tests so we can avoid
        # any real network / env coupling. In production we lazily construct a
        # remote client based on the same environment variables already used
        # by ``services.knot_runtime.remote_runtime``.
        self._llm_client_factory = llm_client_factory

    def generate_candidates(
        self,
        *,
        market: str,
        target_count: int,
        runtime_mode: str = "auto",
    ) -> KnotCandidateSeedResult:
        market_key = (market or "").strip().lower()
        if market_key not in SUPPORTED_MARKETS:
            return KnotCandidateSeedResult(
                available=False,
                fallback_reason=f"unsupported_market:{market}",
            )
        normalized_runtime = (runtime_mode or "auto").strip().lower()
        if normalized_runtime == "off":
            return KnotCandidateSeedResult(
                available=False,
                fallback_reason="knot_runtime_off",
            )
        if normalized_runtime == "local":
            # The local-only knot runtime is a deterministic fallback wrapper
            # and cannot generate fresh candidates; force the caller to use
            # the score-first universe path instead.
            return KnotCandidateSeedResult(
                available=False,
                fallback_reason="local_runtime_cannot_generate",
            )

        bounded_target = max(1, min(int(target_count or 0), MAX_KNOT_TARGET_COUNT))
        client = self._build_client()
        if client is None:
            return KnotCandidateSeedResult(
                available=False,
                fallback_reason="knot_client_unconfigured",
            )

        prompt = self._build_prompt(market=market_key, target_count=bounded_target)
        try:
            raw = client.complete_json("", prompt)
        except (LlmClientError, OSError, ValueError) as exc:
            return KnotCandidateSeedResult(
                available=False,
                fallback_reason=f"knot_call_failed:{exc.__class__.__name__}",
            )
        rows = self._parse_response(raw, market=market_key)
        rows = rows[:bounded_target]
        if not rows:
            return KnotCandidateSeedResult(
                available=False,
                raw_response=json.dumps(raw, ensure_ascii=False) if isinstance(raw, (dict, list)) else str(raw or ""),
                fallback_reason="knot_response_empty",
            )
        return KnotCandidateSeedResult(
            rows=rows,
            runtime_used=self.runtime_id,
            raw_response=json.dumps(raw, ensure_ascii=False) if isinstance(raw, (dict, list)) else str(raw or ""),
            available=True,
        )

    def _build_prompt(self, *, market: str, target_count: int) -> str:
        market_label = "Hong Kong" if market == "hong_kong" else "United States"
        return (
            "You are an institutional research assistant. Propose dynamic short-list candidates "
            f"for the {market_label} equity market that deserve closer follow-up over the next 1-3 trading days.\n"
            f"Return strictly valid JSON: {{\"items\": [<candidate>, ...]}} with at most {target_count} items.\n"
            "Each candidate object must contain these fields:\n"
            "  symbol: ticker in dotted form (e.g. 00700.HK or NVDA.US)\n"
            "  market: 'hong_kong' or 'us'\n"
            "  name: human-readable company name\n"
            "  raw_score: float in [0, 1] reflecting your conviction\n"
            "  rationale: short English sentence explaining why it qualifies now\n"
            "  risk: short English sentence describing the dominant near-term risk\n"
            "  action_hint: short English sentence describing the suggested follow-up action\n"
            "  theme_bucket: one of ai_compute|platform_internet|consumer_growth|financial_defensive|general\n"
            "  risk_level: low|medium|high\n"
            "Constraints:\n"
            f"- Only return symbols that trade in the {market_label} market.\n"
            "- Avoid penny stocks, illiquid tickers, halted names and OTC-only listings.\n"
            "- Prefer names with ongoing catalysts, earnings, or notable flow shifts within the past 5 sessions.\n"
            "- Do not include any commentary, markdown, or text outside the JSON object."
        )

    def _build_client(self) -> Any | None:
        if self._llm_client_factory is not None:
            try:
                return self._llm_client_factory()
            except Exception:
                return None
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
        except Exception:
            return None

    def _parse_response(self, raw: Any, *, market: str) -> list[dict[str, Any]]:
        items = self._extract_items(raw)
        rows: list[dict[str, Any]] = []
        seen_symbols: set[str] = set()
        for item in items:
            if not isinstance(item, Mapping):
                continue
            row_market = str(item.get("market") or "").strip().lower()
            if row_market and row_market != market:
                continue
            row_market = market
            raw_symbol = str(item.get("symbol") or "").strip()
            symbol = normalize_symbol(raw_symbol, row_market)
            if not symbol or "." not in symbol:
                continue
            suffix = symbol.rsplit(".", 1)[-1]
            if row_market == "hong_kong" and suffix != "HK":
                continue
            if row_market == "us" and suffix != "US":
                continue
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            row = {
                "symbol": symbol,
                "market": row_market,
                "candidate_type": "dynamic",
                "name": str(item.get("name") or symbol).strip() or symbol,
                "raw_score": _coerce_float(item.get("raw_score"), default=0.55),
                "rationale": str(item.get("rationale") or "").strip(),
                "risk": str(item.get("risk") or "").strip(),
                "action_hint": str(item.get("action_hint") or "").strip(),
                "theme_bucket": str(item.get("theme_bucket") or "").strip().lower(),
                "risk_level": str(item.get("risk_level") or "").strip().lower(),
                "confidence_source": self.runtime_id,
                "signals": [
                    {
                        "symbol": symbol,
                        "market": row_market,
                        "source": self.runtime_id,
                        "category": "research",
                        "score": _coerce_float(item.get("raw_score"), default=0.55),
                        "summary": str(item.get("rationale") or "knot dynamic candidate seed").strip()
                        or "knot dynamic candidate seed",
                    }
                ],
            }
            rows.append(row)
        return rows

    @staticmethod
    def _extract_items(raw: Any) -> Iterable[Any]:
        if isinstance(raw, Mapping):
            items = raw.get("items")
            if isinstance(items, list):
                return items
            candidates = raw.get("candidates")
            if isinstance(candidates, list):
                return candidates
            data = raw.get("data")
            if isinstance(data, Mapping):
                inner = data.get("items") or data.get("candidates")
                if isinstance(inner, list):
                    return inner
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return []
            return KnotCandidateSeedService._extract_items(parsed)
        return []


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return round(default, 4)
    if score != score:  # NaN guard
        return round(default, 4)
    return round(max(0.0, min(1.0, score)), 4)
