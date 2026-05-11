from __future__ import annotations

from typing import Any, Iterable, Mapping

from vnpy_llm.base import beijing_now_isoformat

from .candidate_framework import TradingCandidateFramework
from .hub import EvaluationHub
from .models import CandidateObservation, PlanAssumption, PlanningArtifact, ResearchFinding


class TradingCandidateSelector:
    """Enhance trading candidate observations with research context and structured review hints."""

    def __init__(
        self,
        framework: TradingCandidateFramework | None = None,
        hub: EvaluationHub | None = None,
    ):
        self.framework = framework or TradingCandidateFramework()
        self.hub = hub or EvaluationHub()

    def build_candidate_artifact(
        self,
        *,
        rows: Iterable[dict[str, Any]],
        research_artifact: PlanningArtifact | None = None,
        preferred_markets: list[str] | None = None,
        max_candidates: int = 5,
        generated_at: str | None = None,
        version: str = "v1",
    ) -> PlanningArtifact:
        generated_at = generated_at or beijing_now_isoformat()
        normalized_rows = [dict(row) for row in rows if isinstance(row, Mapping)]
        row_by_symbol = {
            str(row.get("symbol") or ""): row
            for row in normalized_rows
            if str(row.get("symbol") or "")
        }
        base_observations = self.framework.build_observation_list(
            normalized_rows,
            preferred_markets=preferred_markets,
            max_items=max_candidates,
        )
        enhanced_observations = [
            self._enhance_observation(
                observation,
                row=row_by_symbol.get(observation.symbol, {}),
                research_artifact=research_artifact,
            )
            for observation in base_observations
        ]
        summary = self._summary(enhanced_observations)
        findings = self._build_findings(enhanced_observations)
        suggestions = self.framework.suggest_next_actions(enhanced_observations)
        suggestions.extend(
            self._additional_suggestions(
                enhanced_observations,
                preferred_markets=preferred_markets,
                research_artifact=research_artifact,
            )
        )
        suggestions = list(dict.fromkeys(suggestions))
        risk_prompts = self._risk_prompts(enhanced_observations)
        evidence_items = list(research_artifact.evidence_items) if research_artifact else []
        artifact = self.hub.build_planning_artifact(
            artifact_type="trading_candidate_framework",
            title="Trading Candidate Framework",
            generated_at=generated_at,
            version=version,
            assumptions=self._assumptions(preferred_markets, research_artifact),
            research_findings=findings,
            research_conclusions=[finding.conclusion for finding in findings],
            execution_suggestions=suggestions,
            risk_prompts=risk_prompts,
            invalidation_conditions=self._invalidation_conditions(),
            evidence_items=evidence_items,
            candidate_observations=enhanced_observations,
            meta={
                "framework_summary": summary,
                "framework_rules": self.framework.framework_rules(),
                "preferred_markets": list(preferred_markets or []),
                "research_artifact_attached": research_artifact is not None,
                "next_step_suggestions": suggestions,
                "legacy_artifact_type": "beginner_candidate_framework",
            },
        )
        return artifact

    def _enhance_observation(
        self,
        observation: CandidateObservation,
        *,
        row: dict[str, Any],
        research_artifact: PlanningArtifact | None,
    ) -> CandidateObservation:
        reasons = list(observation.reasons)
        primary_risks = list(observation.primary_risks)
        validation_points = list(observation.validation_points)
        meta = dict(observation.meta)
        llm_research_notes = self._research_notes(row=row, research_artifact=research_artifact, observation=observation)
        research_confidence = self._research_confidence(row=row, notes=llm_research_notes, research_artifact=research_artifact)
        data_completeness = self._data_completeness(row)
        hard_risk_flags = list(observation.hard_risk_flags)
        soft_risk_flags = list(observation.soft_risk_flags)
        manual_review_required = observation.manual_review_required or research_confidence < 0.4 or data_completeness < 0.6

        if llm_research_notes:
            reasons.extend(llm_research_notes)
        else:
            validation_points.append("Add a concise single-name thesis note before promoting the symbol beyond manual review.")
            manual_review_required = True

        structured_risks = self._symbol_specific_risks(row=row, research_artifact=research_artifact, observation=observation)
        for flag in structured_risks["hard"]:
            if flag not in hard_risk_flags:
                hard_risk_flags.append(flag)
        for flag in structured_risks["soft"]:
            if flag not in soft_risk_flags:
                soft_risk_flags.append(flag)

        if structured_risks["notes"]:
            primary_risks.extend(structured_risks["notes"])
        if data_completeness < 0.6:
            primary_risks.append("The local candidate row is missing part of the structured score, rationale, or signal context needed for trading review.")
        if hard_risk_flags:
            validation_points.append("Resolve hard risk flags before moving the symbol into an executable or simulation-ready bucket.")
        if soft_risk_flags:
            validation_points.append("Re-check sizing, liquidity assumptions, and volatility controls because soft risk flags remain active.")

        selected_as = observation.selected_as
        bucket = observation.bucket
        if "thin_liquidity" in hard_risk_flags and bucket != "exclude":
            bucket = "exclude"
            selected_as = self.framework._legacy_selected_as(bucket)

        meta.update(
            {
                "original_selected_as": observation.selected_as,
                "bucket": bucket,
                "legacy_selected_as": selected_as,
                "needs_llm_research": not bool(llm_research_notes),
                "llm_research_connected": bool(llm_research_notes),
                "llm_research_notes": llm_research_notes[:3],
                "data_completeness": data_completeness,
                "selector_version": "trading_candidate_selector_v1",
                "research_confidence": research_confidence,
                "hard_risk_flags": hard_risk_flags,
                "soft_risk_flags": soft_risk_flags,
                "manual_review_required": manual_review_required,
            }
        )
        reasons = list(dict.fromkeys(item for item in reasons if item))[:7]
        primary_risks = list(dict.fromkeys(item for item in primary_risks if item))[:6]
        validation_points = list(dict.fromkeys(item for item in validation_points if item))[:7]
        return CandidateObservation(
            symbol=observation.symbol,
            market=observation.market,
            selected_as=selected_as,
            score=observation.score,
            reasons=reasons,
            primary_risks=primary_risks,
            validation_points=validation_points,
            bucket=bucket,
            trading_level=observation.trading_level,
            hard_risk_flags=hard_risk_flags,
            soft_risk_flags=soft_risk_flags,
            research_confidence=research_confidence,
            liquidity_score=observation.liquidity_score,
            risk_penalty=observation.risk_penalty,
            raw_score=observation.raw_score,
            rank_score=observation.rank_score,
            backtest_ready=observation.backtest_ready,
            manual_review_required=manual_review_required,
            meta=meta,
        )

    def _research_notes(
        self,
        *,
        row: dict[str, Any],
        research_artifact: PlanningArtifact | None,
        observation: CandidateObservation,
    ) -> list[str]:
        notes: list[str] = []
        for key in ("llm_reason", "llm_summary", "research_note", "research_summary", "analysis_summary", "explanation_summary"):
            value = str(row.get(key) or "").strip()
            if value:
                notes.append(value)
        if research_artifact is not None:
            for finding in research_artifact.research_findings:
                if self._matches_observation(finding.conclusion, observation, row):
                    notes.append(finding.conclusion)
        return list(dict.fromkeys(notes))[:3]

    def _research_confidence(
        self,
        *,
        row: dict[str, Any],
        notes: list[str],
        research_artifact: PlanningArtifact | None,
    ) -> float:
        score = 0.2
        if notes:
            score += 0.35
        if bool(row.get("explanation_ready")):
            score += 0.2
        if research_artifact is not None:
            score += 0.15
        if str(row.get("research_note") or "").strip() or str(row.get("analysis_summary") or "").strip():
            score += 0.1
        return round(min(score, 0.95), 2)

    def _symbol_specific_risks(
        self,
        *,
        row: dict[str, Any],
        research_artifact: PlanningArtifact | None,
        observation: CandidateObservation,
    ) -> dict[str, list[str]]:
        notes: list[str] = []
        hard: list[str] = []
        soft: list[str] = []
        base_risk = str(row.get("risk") or "").strip()
        if base_risk:
            notes.append(base_risk)
        haystack = " ".join(
            str(row.get(key) or "")
            for key in ("risk", "rationale", "action_hint", "liquidity_note")
        ).lower()
        if any(keyword in haystack for keyword in ("illiquid", "low liquidity", "wide spread", "thin", "流动性不足", "缺少流动性")):
            hard.append("thin_liquidity")
        if any(keyword in haystack for keyword in ("stale", "outdated", "过期", "陈旧")):
            hard.append("stale_data")
        if any(keyword in haystack for keyword in ("无法交易", "cannot trade", "execution unavailable")):
            hard.append("execution_unavailable")
        if any(keyword in haystack for keyword in ("估值", "valuation", "高估")):
            soft.append("valuation_stretch")
        if any(keyword in haystack for keyword in ("政策", "regulation", "监管")):
            soft.append("policy_uncertainty")
        if any(keyword in haystack for keyword in ("高波动", "volatility", "高beta", "speculative")):
            soft.append("high_volatility")
        if research_artifact is not None:
            for prompt in research_artifact.risk_prompts:
                if self._matches_observation(prompt, observation, row):
                    notes.append(prompt)
        return {
            "notes": list(dict.fromkeys(notes)),
            "hard": list(dict.fromkeys(hard)),
            "soft": list(dict.fromkeys(soft)),
        }

    def _build_findings(self, observations: list[CandidateObservation]) -> list[ResearchFinding]:
        summary = self._summary(observations)
        findings = [
            ResearchFinding(
                topic="candidate_framework",
                conclusion=(
                    f"The current trading candidate framework produced {summary['counts']['priority_trade']} priority-trade names, "
                    f"{summary['counts']['active_watch']} active-watch names, {summary['counts']['research_queue']} research-queue names, "
                    f"and {summary['counts']['exclude']} excluded names."
                ),
                evidence_level="mid",
                source_kind="local_candidate_rules",
                confidence=0.78,
                verification_status="verified",
                invalidation_conditions=[
                    "If candidate inputs or risk notes change materially, regenerate the candidate framework before using it in planning.",
                ],
            )
        ]
        if summary["manual_review_required_count"] > 0:
            findings.append(
                ResearchFinding(
                    topic="candidate_manual_review",
                    conclusion=(
                        f"{summary['manual_review_required_count']} candidate observations still require manual review notes, "
                        "but remain eligible for tracking when no hard risk block exists."
                    ),
                    evidence_level="mid",
                    source_kind="local_candidate_rules",
                    confidence=0.72,
                    verification_status="verified",
                    invalidation_conditions=[
                        "Once thesis notes and liquidity checks are refreshed, the symbol can be reassessed without changing the upstream scoring row.",
                    ],
                )
            )
        return findings

    def _summary(self, observations: list[CandidateObservation]) -> dict[str, Any]:
        summary = self.framework.summary(observations)
        summary["llm_research_pending_count"] = sum(1 for item in observations if item.meta.get("needs_llm_research"))
        summary["low_data_quality_count"] = sum(1 for item in observations if float(item.meta.get("data_completeness") or 0.0) < 0.6)
        summary["manual_review_required_count"] = sum(1 for item in observations if item.manual_review_required)
        return summary

    def _additional_suggestions(
        self,
        observations: list[CandidateObservation],
        *,
        preferred_markets: list[str] | None,
        research_artifact: PlanningArtifact | None,
    ) -> list[str]:
        suggestions: list[str] = []
        if any(item.manual_review_required for item in observations):
            suggestions.append("Add concise single-name review notes for manual-review candidates before discussing stage promotion.")
        if any(item.effective_bucket() == "research_queue" for item in observations):
            suggestions.append("Keep research-queue names visible until cadence, liquidity, and thesis checks are refreshed.")
        if any(item.hard_risk_flags for item in observations):
            suggestions.append("Resolve hard risk flags before moving excluded names back into active review or backtest scopes.")
        if preferred_markets:
            suggestions.append("Keep the candidate review universe aligned with the preferred markets until the routine is stable.")
        if research_artifact is None:
            suggestions.append("Attach the latest research artifact so candidate notes can reuse the same evidence language.")
        return suggestions

    def _risk_prompts(self, observations: list[CandidateObservation]) -> list[str]:
        prompts: list[str] = []
        for item in observations:
            if item.effective_bucket() != "priority_trade":
                prompts.append(f"{item.symbol} is not yet a priority-trade symbol; keep it in watch, research, or exclude mode.")
            if item.manual_review_required:
                prompts.append(f"{item.symbol} still requires manual thesis review before promotion decisions.")
            prompts.extend(item.primary_risks[:2])
        return list(dict.fromkeys(prompts))[:12]

    def _assumptions(
        self,
        preferred_markets: list[str] | None,
        research_artifact: PlanningArtifact | None,
    ) -> list[PlanAssumption]:
        return [
            PlanAssumption(
                "candidate_scope",
                ",".join(preferred_markets or []) or "all_local_markets",
                "Keep the active review universe aligned with the preferred market set while the trading routine stabilizes.",
            ),
            PlanAssumption(
                "selection_method",
                "rules_plus_structured_review_overlay",
                "Primary buckets come from trading rules; research notes and explanation quality only affect manual review priority.",
            ),
            PlanAssumption(
                "research_attachment",
                "attached" if research_artifact is not None else "missing",
                "The candidate framework is stronger when it can reuse the same evidence and risk language as the research artifact.",
            ),
        ]

    def _invalidation_conditions(self) -> list[str]:
        return [
            "If the local candidate feed no longer contains stable score, rationale, or signal data, regenerate the candidate framework before reuse.",
            "If hard risk flags such as thin liquidity or unsupported execution remain unresolved, keep the symbol outside active trading buckets.",
            "If liquidity or volatility checks are stale, do not promote the symbol toward simulation planning.",
        ]

    def _matches_observation(self, text: str, observation: CandidateObservation, row: dict[str, Any]) -> bool:
        haystack = str(text or "").lower()
        if not haystack:
            return False
        symbol = observation.symbol.lower()
        if symbol in haystack or symbol.split(".", 1)[0] in haystack:
            return True
        name = str(row.get("name") or "").strip().lower()
        return bool(name and name in haystack)

    def _data_completeness(self, row: dict[str, Any]) -> float:
        fields = [
            bool(str(row.get("symbol") or "").strip()),
            bool(str(row.get("market") or "").strip()),
            row.get("raw_score") not in {None, ""},
            bool(str(row.get("rationale") or "").strip()),
            bool(str(row.get("risk") or "").strip()),
            bool(row.get("signals")),
            bool(str(row.get("action_hint") or "").strip()),
        ]
        return round(sum(1 for item in fields if item) / len(fields), 2)


BeginnerCandidateSelector = TradingCandidateSelector
