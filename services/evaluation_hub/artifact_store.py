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
        self._write_json(path, payload)
        return path

    def save_workflow_summary(self, workflow: WorkflowRunResult, *, slug: str) -> Path:
        stamped = self._stamped_name(slug, suffix="workflow")
        path = self.root / stamped
        payload = asdict(workflow)
        payload.setdefault("warnings", [])
        payload["warnings"] = list(dict.fromkeys([*payload["warnings"], *self._workflow_confirmation_prompts(workflow)]))
        self._write_json(path, payload)
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
