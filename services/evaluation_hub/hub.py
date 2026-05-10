from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Sequence

from .models import (
    CandidateObservation,
    CapabilityDefinition,
    CapabilityGap,
    ConflictNote,
    EvaluationBundle,
    EvaluationSignal,
    EvidenceItem,
    ExplanationSection,
    PlanAssumption,
    PlanPhase,
    PlanningArtifact,
    ReadinessChecklist,
    RenderedDocument,
    ResearchFinding,
    RiskBudget,
    WorkflowRunResult,
)


class EvaluationHub:
    def merge(self, signals: Iterable[EvaluationSignal]) -> list[EvaluationBundle]:
        grouped: dict[tuple[str, str], list[EvaluationSignal]] = defaultdict(list)
        for signal in signals:
            grouped[(signal.symbol, signal.market)].append(signal)
        return [EvaluationBundle(symbol=symbol, market=market, signals=items) for (symbol, market), items in grouped.items()]

    def build_planning_artifact(
        self,
        *,
        artifact_type: str,
        title: str,
        generated_at: str,
        version: str = "v1",
        assumptions: Sequence[PlanAssumption] | None = None,
        knowledge_sections: Sequence[ExplanationSection] | None = None,
        research_findings: Sequence[ResearchFinding] | None = None,
        research_conclusions: Sequence[str] | None = None,
        execution_suggestions: Sequence[str] | None = None,
        risk_prompts: Sequence[str] | None = None,
        invalidation_conditions: Sequence[str] | None = None,
        evidence_items: Sequence[EvidenceItem] | None = None,
        conflicts: Sequence[ConflictNote] | None = None,
        candidate_observations: Sequence[CandidateObservation] | None = None,
        capability_map: Sequence[CapabilityDefinition] | None = None,
        capability_gaps: Sequence[CapabilityGap] | None = None,
        plan_phases: Sequence[PlanPhase] | None = None,
        risk_budget: RiskBudget | None = None,
        readiness: ReadinessChecklist | None = None,
        workflow: WorkflowRunResult | None = None,
        rendered_documents: Sequence[RenderedDocument] | None = None,
        meta: dict | None = None,
    ) -> PlanningArtifact:
        return PlanningArtifact(
            artifact_type=artifact_type,
            title=title,
            generated_at=generated_at,
            version=version,
            assumptions=list(assumptions or []),
            knowledge_sections=list(knowledge_sections or []),
            research_findings=list(research_findings or []),
            research_conclusions=list(research_conclusions or []),
            execution_suggestions=list(execution_suggestions or []),
            risk_prompts=list(risk_prompts or []),
            invalidation_conditions=list(invalidation_conditions or []),
            evidence_items=list(evidence_items or []),
            conflicts=list(conflicts or []),
            candidate_observations=list(candidate_observations or []),
            capability_map=list(capability_map or []),
            capability_gaps=list(capability_gaps or []),
            plan_phases=list(plan_phases or []),
            risk_budget=risk_budget,
            readiness=readiness,
            workflow=workflow,
            rendered_documents=list(rendered_documents or []),
            meta=dict(meta or {}),
        )

    def merge_evidence(self, *groups: Sequence[EvidenceItem]) -> list[EvidenceItem]:
        merged: list[EvidenceItem] = []
        seen: set[tuple[str, str]] = set()
        for group in groups:
            for item in group:
                key = (item.title.strip(), item.source_url.strip())
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return merged

    def collect_risk_prompts(self, artifact: PlanningArtifact) -> list[str]:
        prompts = list(artifact.risk_prompts)
        prompts.extend(artifact.invalidation_conditions)
        if artifact.risk_budget:
            prompts.extend(artifact.risk_budget.stop_conditions)
        if artifact.readiness and not artifact.readiness.passed:
            prompts.extend(item.details for item in artifact.readiness.failed_items() if item.details)
        return prompts
