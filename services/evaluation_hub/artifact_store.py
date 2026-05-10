from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import PlanAssumption, PlanningArtifact, RiskBudget, WorkflowRunResult, WorkflowStepResult


class ArtifactStore:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)
        self.root = self.repo_root / "state" / "runs" / "quant_workflow"

    def save_artifact(self, artifact: PlanningArtifact, *, slug: str) -> Path:
        stamped = self._stamped_name(slug, suffix="artifact")
        path = self.root / stamped
        payload = artifact.to_dict()
        payload.setdefault("meta", {})
        payload["meta"].setdefault("next_step_suggestions", self._next_step_suggestions(artifact))
        payload["meta"].setdefault("confirmation_requirements", self._confirmation_requirements(artifact))
        payload["meta"].setdefault("rendered_formats", self._rendered_formats(artifact))
        payload["meta"].setdefault("risk_labels", self._artifact_risk_labels(artifact))
        payload["meta"].setdefault("artifact_summary", self._artifact_summary(artifact))
        payload["meta"].setdefault("traceability", self._artifact_traceability(artifact, slug=slug))
        self._write_json(path, payload)
        self._update_latest_index(
            bucket="artifacts",
            slug=slug,
            entry=self._artifact_index_entry(path=path, artifact=artifact, payload=payload),
        )
        return path

    def save_workflow_summary(self, workflow: WorkflowRunResult, *, slug: str) -> Path:
        stamped = self._stamped_name(slug, suffix="workflow")
        path = self.root / stamped
        payload = asdict(workflow)
        payload.setdefault("warnings", [])
        payload["warnings"] = list(dict.fromkeys([*payload["warnings"], *self._workflow_confirmation_prompts(workflow)]))
        payload["workflow_summary"] = self._workflow_summary(workflow)
        payload["traceability"] = self._workflow_traceability(workflow, slug=slug)
        self._write_json(path, payload)
        self._update_latest_index(
            bucket="workflow_reports",
            slug=slug,
            entry=self._workflow_index_entry(path=path, workflow=workflow, payload=payload),
        )
        return path

    def build_step(
        self,
        *,
        step: str,
        status: str,
        message: str,
        outputs: list[str] | None = None,
        warnings: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> WorkflowStepResult:
        return WorkflowStepResult(
            step=step,
            status=status,
            message=message,
            outputs=list(outputs or []),
            warnings=list(warnings or []),
            meta=dict(meta or {}),
        )

    def latest_artifact_path(self, *, slug: str) -> Path | None:
        if not self.root.exists():
            return None
        prefix = f"{self._clean_slug(slug)}_artifact_"
        candidates = sorted(self.root.glob(f"{prefix}*.json"))
        return candidates[-1] if candidates else None

    def load_previous_plan(self, *, slug: str) -> PlanningArtifact | None:
        path = self.latest_artifact_path(slug=slug)
        if path is None or not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return self._planning_artifact_from_payload(payload)

    def _stamped_name(self, slug: str, *, suffix: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        clean = self._clean_slug(slug)
        return f"{clean}_{suffix}_{ts}.json"

    def _clean_slug(self, slug: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in slug).strip("_") or "quant"

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=self._default_json) + "\n", encoding="utf-8")

    def _default_json(self, value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, Path):
            return str(value)
        return str(value)

    def _planning_artifact_from_payload(self, payload: dict[str, Any]) -> PlanningArtifact:
        assumptions = [PlanAssumption(**item) for item in payload.get("assumptions", [])]
        risk_budget_payload = payload.get("risk_budget")
        risk_budget = RiskBudget(**risk_budget_payload) if isinstance(risk_budget_payload, dict) else None
        return PlanningArtifact(
            artifact_type=str(payload.get("artifact_type") or "beginner_quant_plan"),
            title=str(payload.get("title") or "Beginner Quant Personal Plan"),
            generated_at=str(payload.get("generated_at") or ""),
            version=str(payload.get("version") or "v1"),
            assumptions=assumptions,
            risk_budget=risk_budget,
            meta=dict(payload.get("meta") or {}),
        )

    def _next_step_suggestions(self, artifact: PlanningArtifact) -> list[str]:
        suggestions = list(artifact.execution_suggestions)
        if artifact.readiness and not artifact.readiness.passed:
            suggestions.append("Resolve readiness failures before upgrading to the next stage.")
        if artifact.capability_gaps:
            suggestions.append("Keep capability gaps visible and use manual alternatives where needed.")
        return list(dict.fromkeys(suggestions))

    def _confirmation_requirements(self, artifact: PlanningArtifact) -> list[str]:
        requirements: list[str] = []
        for cap in artifact.capability_map:
            if cap.requires_confirmation:
                requirements.append(f"Confirmation required before using {cap.path}.")
            if cap.mutates_state:
                requirements.append(f"{cap.path} may change local state or reports.")
            if cap.connects_remote:
                requirements.append(f"{cap.path} may connect to remote/OpenD or market data services.")
        return list(dict.fromkeys(requirements))

    def _workflow_confirmation_prompts(self, workflow: WorkflowRunResult) -> list[str]:
        prompts: list[str] = []
        for step in workflow.steps:
            if step.meta.get("requires_confirmation"):
                prompts.append(f"Step {step.step} requires explicit user confirmation before execution.")
        return prompts

    def _rendered_formats(self, artifact: PlanningArtifact) -> list[str]:
        return list(dict.fromkeys(item.format for item in artifact.rendered_documents if getattr(item, "format", "")))

    def _artifact_risk_labels(self, artifact: PlanningArtifact) -> list[str]:
        labels: list[str] = []
        if artifact.risk_prompts:
            labels.append("risk_prompts_present")
        if artifact.readiness and not artifact.readiness.passed:
            labels.append("readiness_blocked")
        if artifact.capability_gaps:
            labels.append("capability_gaps_present")
        if self._confirmation_requirements(artifact):
            labels.append("confirmation_required")
        return labels

    def _artifact_summary(self, artifact: PlanningArtifact) -> dict[str, Any]:
        return {
            "artifact_type": artifact.artifact_type,
            "title": artifact.title,
            "generated_at": artifact.generated_at,
            "version": artifact.version,
            "knowledge_section_count": len(artifact.knowledge_sections),
            "research_finding_count": len(artifact.research_findings),
            "candidate_count": len(artifact.candidate_observations),
            "plan_phase_count": len(artifact.plan_phases),
            "rendered_document_count": len(artifact.rendered_documents),
            "has_risk_budget": artifact.risk_budget is not None,
            "has_readiness": artifact.readiness is not None,
        }

    def _artifact_traceability(self, artifact: PlanningArtifact, *, slug: str) -> dict[str, Any]:
        return {
            "slug": self._clean_slug(slug),
            "artifact_type": artifact.artifact_type,
            "version": artifact.version,
            "generated_at": artifact.generated_at,
            "current_assumption_count": len(artifact.assumptions),
            "invalidation_condition_count": len(artifact.invalidation_conditions),
            "confirmation_requirement_count": len(self._confirmation_requirements(artifact)),
            "next_step_count": len(self._next_step_suggestions(artifact)),
        }

    def _workflow_summary(self, workflow: WorkflowRunResult) -> dict[str, Any]:
        blocked_steps = [step.step for step in workflow.steps if step.status == "blocked"]
        confirmation_steps = [step.step for step in workflow.steps if step.meta.get("requires_confirmation")]
        warning_steps = [step.step for step in workflow.steps if step.warnings]
        return {
            "step_count": len(workflow.steps),
            "output_count": len(workflow.outputs),
            "warning_count": len(workflow.warnings),
            "blocked_steps": blocked_steps,
            "confirmation_required_steps": confirmation_steps,
            "warning_steps": warning_steps,
        }

    def _workflow_traceability(self, workflow: WorkflowRunResult, *, slug: str) -> dict[str, Any]:
        return {
            "slug": self._clean_slug(slug),
            "workflow_name": workflow.workflow_name,
            "mode": workflow.mode,
            "started_at": workflow.started_at,
            "status": workflow.status,
            "assumption_count": len(workflow.assumptions),
        }

    def _artifact_index_entry(self, *, path: Path, artifact: PlanningArtifact, payload: dict[str, Any]) -> dict[str, Any]:
        meta = dict(payload.get("meta") or {})
        return {
            "path": str(path),
            "artifact_type": artifact.artifact_type,
            "title": artifact.title,
            "generated_at": artifact.generated_at,
            "version": artifact.version,
            "artifact_summary": dict(meta.get("artifact_summary") or {}),
            "traceability": dict(meta.get("traceability") or {}),
            "risk_labels": list(meta.get("risk_labels") or []),
        }

    def _workflow_index_entry(self, *, path: Path, workflow: WorkflowRunResult, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": str(path),
            "workflow_name": workflow.workflow_name,
            "mode": workflow.mode,
            "started_at": workflow.started_at,
            "status": workflow.status,
            "workflow_summary": dict(payload.get("workflow_summary") or {}),
            "traceability": dict(payload.get("traceability") or {}),
        }

    def _latest_index_path(self) -> Path:
        return self.root / "latest_index.json"

    def _load_latest_index(self) -> dict[str, Any]:
        path = self._latest_index_path()
        if not path.exists():
            return {"artifacts": {}, "workflow_reports": {}}
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("artifacts", {})
        payload.setdefault("workflow_reports", {})
        return payload

    def _update_latest_index(self, *, bucket: str, slug: str, entry: dict[str, Any]) -> None:
        payload = self._load_latest_index()
        payload.setdefault(bucket, {})
        payload[bucket][self._clean_slug(slug)] = entry
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json(self._latest_index_path(), payload)
