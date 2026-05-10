from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .candidate_framework import BeginnerCandidateFramework
from .hub import EvaluationHub
from .models import CandidateObservation, PlanAssumption, PlanningArtifact, ResearchFinding


class BeginnerCandidateSelector:
    """Enhance the baseline beginner candidate framework with richer downgrade logic."""

    def __init__(
        self,
        framework: BeginnerCandidateFramework | None = None,
        hub: EvaluationHub | None = None,
    ):
        self.framework = framework or BeginnerCandidateFramework()
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
        generated_at = generated_at or datetime.now(timezone.utc).isoformat()
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
            artifact_type="beginner_candidate_framework",
            title="Beginner Candidate Framework",
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
        if llm_research_notes:
            reasons.extend(llm_research_notes)
        else:
            validation_points.append("Add a single-name explanation from scripts/llm or curated research before any execution-stage upgrade.")

        if not self._has_minimum_data(row):
            primary_risks.append("The local candidate row is missing score, rationale, or signal detail needed for a beginner-friendly review.")
            selected_as = "validate_only"
        else:
            selected_as = observation.selected_as

        symbol_specific_risks = self._symbol_specific_risks(row=row, research_artifact=research_artifact, observation=observation)
        primary_risks.extend(symbol_specific_risks)
        if selected_as == "beginner_watchlist" and not llm_research_notes:
            selected_as = "observe_only"
        if selected_as == "observe_only" and len(symbol_specific_risks) >= 2:
            selected_as = "validate_only"

        if self._mentions_missing_liquidity(row):
            selected_as = "validate_only"
            primary_risks.append("Current local inputs do not provide enough liquidity comfort for a beginner-safe watchlist entry.")
            validation_points.append("Confirm liquidity, spread, and trading range with a fresh quote snapshot before keeping this symbol active.")

        data_completeness = self._data_completeness(row)
        meta.update(
            {
                "original_selected_as": observation.selected_as,
                "beginner_bucket": self._beginner_bucket(selected_as),
                "needs_llm_research": not bool(llm_research_notes),
                "llm_research_connected": bool(llm_research_notes),
                "llm_research_notes": llm_research_notes[:3],
                "data_completeness": data_completeness,
                "selector_version": "candidate_selector_v1",
            }
        )
        reasons = list(dict.fromkeys(item for item in reasons if item))[:6]
        primary_risks = list(dict.fromkeys(item for item in primary_risks if item))[:5]
        validation_points = list(dict.fromkeys(item for item in validation_points if item))[:6]
        return CandidateObservation(
            symbol=observation.symbol,
            market=observation.market,
            selected_as=selected_as,
            score=observation.score,
            reasons=reasons,
            primary_risks=primary_risks,
            validation_points=validation_points,
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
        for key in ("llm_reason", "llm_summary", "research_note", "research_summary", "analysis_summary"):
            value = str(row.get(key) or "").strip()
            if value:
                notes.append(value)
        if research_artifact is not None:
            for finding in research_artifact.research_findings:
                if self._matches_observation(finding.conclusion, observation, row):
                    notes.append(finding.conclusion)
        return list(dict.fromkeys(notes))[:3]

    def _symbol_specific_risks(
        self,
        *,
        row: dict[str, Any],
        research_artifact: PlanningArtifact | None,
        observation: CandidateObservation,
    ) -> list[str]:
        risks: list[str] = []
        base_risk = str(row.get("risk") or "").strip()
        if base_risk:
            risks.append(base_risk)
        if research_artifact is not None:
            for prompt in research_artifact.risk_prompts:
                if self._matches_observation(prompt, observation, row):
                    risks.append(prompt)
        return list(dict.fromkeys(risks))

    def _build_findings(self, observations: list[CandidateObservation]) -> list[ResearchFinding]:
        summary = self._summary(observations)
        findings = [
            ResearchFinding(
                topic="candidate_framework",
                conclusion=(
                    f"The current beginner candidate framework produced {summary['counts']['beginner_watchlist']} watchlist names, "
                    f"{summary['counts']['observe_only']} observe-only names, and {summary['counts']['validate_only']} validate-only names."
                ),
                evidence_level="mid",
                source_kind="local_candidate_rules",
                confidence=0.76,
                verification_status="verified",
                invalidation_conditions=[
                    "If candidate inputs or risk notes change materially, regenerate the candidate framework before using it in planning.",
                ],
            )
        ]
        if summary["llm_research_pending_count"] > 0:
            findings.append(
                ResearchFinding(
                    topic="candidate_explanation_gap",
                    conclusion=(
                        f"{summary['llm_research_pending_count']} candidate observations still need a single-name research explanation "
                        "before they should be treated as stage-upgrade candidates."
                    ),
                    evidence_level="mid",
                    source_kind="local_candidate_rules",
                    confidence=0.72,
                    verification_status="verified",
                    invalidation_conditions=[
                        "Once a symbol has a stable explanation and refreshed liquidity check, it can be reassessed for a higher bucket.",
                    ],
                )
            )
        return findings

    def _summary(self, observations: list[CandidateObservation]) -> dict[str, Any]:
        summary = self.framework.summary(observations)
        summary["llm_research_pending_count"] = sum(1 for item in observations if item.meta.get("needs_llm_research"))
        summary["low_data_quality_count"] = sum(1 for item in observations if float(item.meta.get("data_completeness") or 0.0) < 0.6)
        return summary

    def _additional_suggestions(
        self,
        observations: list[CandidateObservation],
        *,
        preferred_markets: list[str] | None,
        research_artifact: PlanningArtifact | None,
    ) -> list[str]:
        suggestions: list[str] = []
        if any(item.meta.get("needs_llm_research") for item in observations):
            suggestions.append("Fill in single-name explanation notes for the remaining observation names before any stage upgrade discussion.")
        if any(item.selected_as == "validate_only" for item in observations):
            suggestions.append("Keep validate-only names in a research queue until liquidity, volatility, and thesis checks are refreshed.")
        if preferred_markets:
            suggestions.append("Keep the candidate review universe aligned with the preferred markets until the routine is stable.")
        if research_artifact is None:
            suggestions.append("Attach the latest beginner research artifact so candidate explanations can reuse the same evidence language.")
        return suggestions

    def _risk_prompts(self, observations: list[CandidateObservation]) -> list[str]:
        prompts: list[str] = []
        for item in observations:
            if item.selected_as != "beginner_watchlist":
                prompts.append(f"{item.symbol} is not yet a beginner_watchlist symbol; keep it in observation or validation mode.")
            if item.meta.get("needs_llm_research"):
                prompts.append(f"{item.symbol} still lacks a single-name research explanation from local research tooling.")
            prompts.extend(item.primary_risks[:2])
        return list(dict.fromkeys(prompts))[:10]

    def _assumptions(
        self,
        preferred_markets: list[str] | None,
        research_artifact: PlanningArtifact | None,
    ) -> list[PlanAssumption]:
        return [
            PlanAssumption(
                "candidate_scope",
                ",".join(preferred_markets or []) or "all_local_markets",
                "Keep the first review universe small and aligned with the preferred market set.",
            ),
            PlanAssumption(
                "selection_method",
                "rules_plus_explanation_gate",
                "A symbol can be downgraded when explanation quality, liquidity comfort, or data completeness is not strong enough.",
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
            "If the watchlist starts depending on names without explainable thesis notes, downgrade them to observe_only or validate_only.",
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

    def _has_minimum_data(self, row: dict[str, Any]) -> bool:
        if not row:
            return False
        return all(
            [
                str(row.get("symbol") or "").strip(),
                str(row.get("market") or "").strip(),
                any(
                    [
                        row.get("raw_score") not in {None, ""},
                        bool(row.get("signals")),
                    ]
                ),
                any(
                    [
                        str(row.get("rationale") or "").strip(),
                        str(row.get("action_hint") or "").strip(),
                    ]
                ),
            ]
        )

    def _mentions_missing_liquidity(self, row: dict[str, Any]) -> bool:
        text = " ".join(
            str(row.get(key) or "")
            for key in ("risk", "rationale", "action_hint", "liquidity_note")
        ).lower()
        if not text:
            return False
        keywords = ("illiquid", "low liquidity", "wide spread", "thin", "缺少流动性", "流动性不足")
        return any(keyword in text for keyword in keywords)

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

    def _beginner_bucket(self, selected_as: str) -> str:
        if selected_as == "beginner_watchlist":
            return "review_ready"
        if selected_as == "observe_only":
            return "watch_and_explain"
        return "validation_queue"
