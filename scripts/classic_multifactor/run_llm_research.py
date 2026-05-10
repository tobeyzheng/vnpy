"""Standalone LLM research entry for classic_multifactor.

Reuses project LLM/KnotAgent client (vnpy_llm.llm_client.OpenAICompatibleClient)
to run a single web-search-enabled query and persist the structured JSON
answer under state/runs/llm_research/.

This script does NOT:
  * connect to OpenD / Futu
  * submit any SIM / REAL order
  * mutate account / order / reconciliation state

It only writes a research artifact file and prints a short summary.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy_llm.config import load_config
from vnpy_llm.llm_client import LlmClientError, OpenAICompatibleClient


SCHEMA_PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        "keys": ["topic", "key_findings", "recommendations", "risks", "references"],
        "system": (
            "You are QuantResearchAgent. Answer ONLY with a single JSON object. "
            "Do not output Markdown. Cite sources in `references` with url and "
            "published_at; if no reliable source is found set evidence_level=low."
        ),
    },
    "factor": {
        "keys": [
            "topic",
            "symbol",
            "timeframe",
            "key_findings",
            "factor_recommendations",
            "parameter_grids",
            "risks_and_caveats",
            "references",
        ],
        "system": (
            "You are QuantResearchAgent specialised in intraday multi-factor "
            "research. Output ONLY one JSON object. For each recommended "
            "parameter give rationale and evidence_level in {high,mid,low}. "
            "Never fabricate URLs. `parameter_grids` must be an object of "
            "{param_name: [values]}."
        ),
    },
    "tune": {
        "keys": [
            "topic",
            "symbol",
            "timeframe",
            "baseline",
            "tuning_plan",
            "expected_impact",
            "risks",
            "references",
        ],
        "system": (
            "You are QuantTuningAgent. Given the user's baseline and goal, "
            "produce a concrete tuning_plan (list of steps with params and "
            "grid). Output ONLY one JSON object; cite sources when claims "
            "are based on public research."
        ),
    },
    "beginner_quant": {
        "keys": [
            "topic",
            "core_concepts",
            "research_workflow",
            "risk_control",
            "performance_evaluation",
            "beginner_safe_practices",
            "common_misunderstandings",
            "learning_sequence",
            "practice_sequence",
            "minimum_viable_start",
            "conflicting_viewpoints",
            "low_confidence_items",
            "references",
        ],
        "system": (
            "You are QuantBeginnerResearchAgent. Summarise public and verifiable knowledge "
            "about quantitative trading for a beginner. Output ONLY one JSON object. "
            "Separate verified findings, conflicting viewpoints, low-confidence items, "
            "and references. Do not fabricate URLs; when evidence is weak, mark "
            "evidence_level=low and verification_status=to_verify. Focus on: "
            "- Core concepts in simple language with examples "
            "- Research workflow from idea to validation "
            "- Risk control principles for beginners "
            "- Performance evaluation beyond simple returns "
            "- Beginner-safe practice order and common pitfalls "
            "- Learning and practice sequences "
            "- Minimum viable starting point "
            "- Conflicting viewpoints in the field "
            "- Items needing verification "
            "Cite public academic papers, industry research, and regulatory guidance "
            "when making claims about best practices."
        ),
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a single LLM (web-search) research query and persist JSON output.",
    )
    parser.add_argument("--config", required=True, help="LLM config JSON path (vnpy_llm.config schema)")
    parser.add_argument("--topic", required=True, help="Research topic in plain text")
    parser.add_argument("--context", default="", help="Extra context string, or path to JSON/text file")
    parser.add_argument("--symbol", default="", help="Optional vt_symbol, e.g. NVDA.SMART")
    parser.add_argument("--timeframe", default="", help="Optional timeframe hint, e.g. 1m/5m/1d")
    parser.add_argument(
        "--schema",
        default="factor",
        choices=sorted(SCHEMA_PRESETS.keys()),
        help="Output schema preset",
    )
    parser.add_argument(
        "--enable-web-search",
        action="store_true",
        help="Force enable web search (otherwise follow config)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Request timeout seconds (min 60 for KnotAgent)",
    )
    parser.add_argument("--output", default="", help="Output JSON path; default under state/runs/llm_research/")
    parser.add_argument("--dry-run", action="store_true", help="Only print prompt, do not call LLM")
    return parser


def _load_context(raw: str) -> Any:
    if not raw:
        return ""
    candidate = Path(raw)
    if candidate.exists() and candidate.is_file():
        text = candidate.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return raw


def _slugify(text: str, limit: int = 40) -> str:
    cleaned = [c.lower() if c.isalnum() else "_" for c in text.strip()]
    slug = "".join(cleaned).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return (slug or "topic")[:limit]


def _default_output(topic: str, symbol: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    parts = [p for p in [symbol.lower().replace(".", "_"), _slugify(topic)] if p]
    filename = f"{'_'.join(parts)}_{ts}.json"
    return PROJECT_ROOT / "state" / "runs" / "llm_research" / filename


def _build_user_prompt(args: argparse.Namespace, preset: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "topic": args.topic,
        "required_keys": preset["keys"],
        "output_format": "single JSON object, no markdown",
    }
    if args.symbol:
        payload["symbol"] = args.symbol
    if args.timeframe:
        payload["timeframe"] = args.timeframe
    context = _load_context(args.context)
    if context:
        payload["context"] = context
    return json.dumps(payload, ensure_ascii=False)


def _summary(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in ("topic", "symbol", "timeframe"):
        if key in result:
            lines.append(f"{key}: {result[key]}")
    findings = result.get("key_findings") or []
    if isinstance(findings, list):
        for idx, item in enumerate(findings[:3], 1):
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            lines.append(f"finding#{idx}: {text[:160]}")
    refs = result.get("references") or []
    if isinstance(refs, list):
        lines.append(f"references: {len(refs)}")
    return lines


def main() -> int:
    args = build_parser().parse_args()
    preset = SCHEMA_PRESETS[args.schema]

    config = load_config(args.config, PROJECT_ROOT)
    timeout = max(args.timeout, config.timeout_seconds, 60)
    client = OpenAICompatibleClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        timeout_seconds=timeout,
        max_retries=config.max_retries,
        api_type=config.llm_api_type,
        api_user=config.api_user,
        enable_web_search=args.enable_web_search or config.enable_web_search,
        temperature=config.temperature,
        conversation_id=config.conversation_id,
    )

    system_prompt = preset["system"]
    user_prompt = _build_user_prompt(args, preset)

    if args.dry_run or not client.is_configured():
        print("[dry-run] system_prompt:")
        print(system_prompt)
        print("[dry-run] user_prompt:")
        print(user_prompt)
        if not client.is_configured():
            print("[dry-run] NOTE: LLM client is not fully configured; real call would be skipped.")
        return 0

    try:
        result = client.complete_json(system_prompt, user_prompt)
    except LlmClientError as exc:
        print(f"[error] LLM call failed: {exc}", file=sys.stderr)
        return 2

    envelope: dict[str, Any] = {
        "agent": "QuantResearchAgent",
        "schema": args.schema,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "request": {
            "topic": args.topic,
            "symbol": args.symbol or None,
            "timeframe": args.timeframe or None,
            "enable_web_search": client.enable_web_search,
            "model": client.model or None,
            "api_type": client.api_type,
        },
        "result": result,
    }

    output_path = Path(args.output) if args.output else _default_output(args.topic, args.symbol)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT.joinpath(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"LLM research artifact written: {output_path}")
    for line in _summary(result):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
