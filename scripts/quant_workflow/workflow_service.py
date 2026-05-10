from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.evaluation_hub import EvaluationHub
from services.evaluation_hub.artifact_store import ArtifactStore
from services.evaluation_hub.beginner_research import BeginnerResearchService
from services.evaluation_hub.candidate_framework import BeginnerCandidateFramework
from services.evaluation_hub.capability_registry import CapabilityRegistry, CapabilityStageResolver
from services.evaluation_hub.doc_renderer import BeginnerExplanationRenderer
from services.evaluation_hub.models import PlanAssumption, PlanningArtifact, WorkflowRunResult, WorkflowStepResult
from services.evaluation_hub.plan_generator import BeginnerPlanGenerator
from services.evaluation_hub.readiness_gate import ReadinessGateService
from services.healthcheck import HealthcheckService
from services.strategy.candidate_provider import UnifiedCandidateProvider


class QuantWorkflowService:
    FULL_PLAN_STEPS = (
        "research",
        "candidate_framework",
        "backtest_validation",
        "planning",
        "execution_boundary",
    )
    STAGE_ONLY_STEPS = {
        "research": ("research",),
        "backtest": ("research", "backtest_validation"),
        "simulation": ("research", "candidate_framework", "backtest_validation", "planning", "execution_boundary"),
        "live": ("research", "candidate_framework", "backtest_validation", "planning", "execution_boundary"),
    }

    def __init__(self, repo_root: Path, *, allow_remote_checks: bool = False):
        self.repo_root = Path(repo_root)
        self.allow_remote_checks = bool(allow_remote_checks)
        self.registry = CapabilityRegistry()
        self.stage_resolver = CapabilityStageResolver(self.registry)
        self.hub = EvaluationHub()
        self.research_service = BeginnerResearchService(self.hub)
        self.renderer = BeginnerExplanationRenderer()
        self.candidate_framework = BeginnerCandidateFramework()
        self.plan_generator = BeginnerPlanGenerator(self.hub)
        self.readiness_gate = ReadinessGateService()
        self.store = ArtifactStore(self.repo_root)

    def run(
        self,
        *,
        workflow_name: str = "beginner_quant",
        mode: str = "plan",
        profile: dict[str, Any] | None = None,
        preferred_markets: list[str] | None = None,
        max_candidates: int = 5,
        stage: str = "research",
    ) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat()
        profile = dict(profile or {})
        requested_steps = list(self._resolve_requested_steps(mode=mode, stage=stage))
        steps: list[WorkflowStepResult] = []
        warnings: list[str] = []
        outputs: list[str] = []
        artifact_paths: dict[str, str] = {}

        preflight = self._build_preflight(mode=mode, stage=stage, requested_steps=requested_steps)
        preflight_step = self.store.build_step(
            step="preflight",
            status=preflight["status"],
            message=preflight["message"],
            warnings=preflight["warnings"],
            meta={
                "requires_confirmation": False,
                "requested_steps": requested_steps,
                "checks": preflight["checks"],
                "blocking_reasons": preflight["blocking_reasons"],
            },
        )
        steps.append(preflight_step)
        warnings.extend(preflight_step.warnings)

        if preflight_step.status == "blocked":
            return self._finalize_workflow(
                workflow_name=workflow_name,
                mode=mode,
                started_at=started_at,
                steps=steps,
                outputs=outputs,
                warnings=warnings,
                assumptions=[],
                artifact_paths=artifact_paths,
            )

        health = self._load_health_snapshot(mode=mode)
        health_step = self.store.build_step(
            step="healthcheck",
            status="ok" if health.get("status") not in {"blocked", "unknown"} else ("warning" if health.get("status") == "unknown" else "blocked"),
            message="Health status collected from cached file or offline placeholder.",
            outputs=[health.get("path")] if health.get("path") else [],
            warnings=[alert.get("message", "") for alert in health.get("alerts", []) if alert.get("message")],
            meta={"requires_confirmation": False, "status": health.get("status"), "source": health.get("source")},
        )
        steps.append(health_step)
        warnings.extend(health_step.warnings)

        capability_map = self.registry.list_all()
        stage_capabilities = self.registry.select_for_stage(stage, include_unsafe=True)
        capability_gaps = self.registry.capability_gaps()
        stage_map = self.stage_resolver.available_stages(beginner_mode=False)
        capabilities_step = self.store.build_step(
            step="capability_map",
            status="ok",
            message=f"Collected capability map for stage {stage}.",
            warnings=[gap.reason for gap in capability_gaps],
            meta={
                "requires_confirmation": False,
                "current_stage_capabilities": [item.capability_id for item in stage_capabilities],
                "stage_boundary_map": self.registry.stage_boundary_map(),
                "available_stages": {key: [item.capability_id for item in value] for key, value in stage_map.items()},
            },
        )
        steps.append(capabilities_step)
        warnings.extend(capabilities_step.warnings)

        research_artifact: PlanningArtifact | None = None
        if "research" in requested_steps:
            research_artifact = self.research_service.build_default_artifact()
            research_artifact.capability_map = capability_map
            research_artifact.capability_gaps = capability_gaps
            research_artifact.rendered_documents = [
                self.renderer.render_markdown(research_artifact),
                self.renderer.render_json(research_artifact),
            ]
            research_path = self.store.save_artifact(research_artifact, slug="beginner_quant_research")
            artifact_paths["research_artifact"] = str(research_path)
            outputs.append(str(research_path))
            steps.append(
                self.store.build_step(
                    step="research",
                    status="ok",
                    message="Generated beginner research artifact and readable documents.",
                    outputs=[str(research_path)],
                    meta={"requires_confirmation": False},
                )
            )

        observations = []
        if any(step_name in requested_steps for step_name in {"candidate_framework", "planning"}):
            candidates = UnifiedCandidateProvider(self.repo_root).load()
            if preferred_markets:
                filtered = [row for row in candidates if row.get("market") in set(preferred_markets)]
            else:
                filtered = candidates
            observations = self.candidate_framework.build_observation_list(
                filtered,
                preferred_markets=preferred_markets,
                max_items=max_candidates,
            )
            observation_summary = self.candidate_framework.summary(observations)
            if "candidate_framework" in requested_steps:
                steps.append(
                    self.store.build_step(
                        step="candidate_framework",
                        status="ok" if observations else "warning",
                        message="Built candidate observation list.",
                        warnings=[] if observations else ["No candidate observations were produced from the current local inputs."],
                        meta={
                            "requires_confirmation": False,
                            "summary": observation_summary,
                            "next_actions": self.candidate_framework.suggest_next_actions(observations),
                        },
                    )
                )

        backtest_report: dict[str, Any] = {}
        backtest_metadata: dict[str, Any] = {}
        backtest_checklist = None
        if any(step_name in requested_steps for step_name in {"backtest_validation", "planning"}):
            backtest_report = self._load_backtest_report()
            backtest_metadata = self.readiness_gate.normalize_backtest_report(backtest_report) if backtest_report else {}
            backtest_checklist = self.readiness_gate.validate_backtest_metadata(backtest_metadata) if backtest_metadata else None
            if "backtest_validation" in requested_steps:
                validation_warnings = []
                if not backtest_report:
                    validation_warnings.append("No local vn.py backtest report was found for validation.")
                elif backtest_checklist is not None:
                    validation_warnings.extend(item.details for item in backtest_checklist.failed_items() if item.details)
                steps.append(
                    self.store.build_step(
                        step="backtest_validation",
                        status="ok" if backtest_checklist and backtest_checklist.passed else "warning",
                        message="Collected local backtest metadata for stage validation.",
                        outputs=["state/runs/classic_multifactor/vnpy_cta_backtest_report.json"] if backtest_report else [],
                        warnings=validation_warnings,
                        meta={
                            "requires_confirmation": False,
                            "backtest_metadata": backtest_metadata,
                        },
                    )
                )

        plan_artifact: PlanningArtifact | None = None
        if "planning" in requested_steps:
            plan_artifact = self.plan_generator.build_plan(profile=profile, observations=observations)
            plan_artifact.capability_map = capability_map
            plan_artifact.capability_gaps = capability_gaps
            plan_artifact.rendered_documents = [
                self.renderer.render_markdown(plan_artifact),
                self.renderer.render_json(plan_artifact),
            ]
            readiness = self.readiness_gate.build_stage_checklist(
                stage=stage,
                health_status=str(health.get("status") or "ok"),
                has_research_artifact=research_artifact is not None,
                has_backtest_metadata=bool(backtest_checklist and backtest_checklist.passed),
                has_risk_budget=plan_artifact.risk_budget is not None,
                has_review_notes=True,
                capability_gaps=[gap.capability_id for gap in capability_gaps if gap.expected_path.startswith("scripts/run_hk")],
            )
            plan_artifact.readiness = readiness
            plan_path = self.store.save_artifact(plan_artifact, slug="beginner_quant_plan")
            artifact_paths["plan_artifact"] = str(plan_path)
            outputs.append(str(plan_path))
            block_upgrade, reasons = self.readiness_gate.should_block_upgrade(readiness)
            steps.append(
                self.store.build_step(
                    step="planning",
                    status="ok" if not block_upgrade else "blocked",
                    message="Generated personal beginner plan and readiness checklist.",
                    outputs=[str(plan_path)],
                    warnings=reasons,
                    meta={
                        "requires_confirmation": False,
                        "minimum_observation_requirements": self.readiness_gate.minimum_observation_requirements(stage),
                    },
                )
            )
            warnings.extend(reasons)

        if "execution_boundary" in requested_steps:
            execution_caps = [cap for cap in capability_map if cap.stage in {"simulation", "live"}]
            if execution_caps:
                steps.append(
                    self.store.build_step(
                        step="execution_boundary",
                        status="planned",
                        message="Execution-capable entries were detected but not run.",
                        warnings=[f"Confirmation required before using {cap.path}" for cap in execution_caps],
                        meta={
                            "requires_confirmation": True,
                            "capabilities": [cap.capability_id for cap in execution_caps],
                        },
                    )
                )
                warnings.extend([f"Execution not started: {cap.path}" for cap in execution_caps])

        assumptions = self._collect_assumptions(plan_artifact=plan_artifact, research_artifact=research_artifact)
        return self._finalize_workflow(
            workflow_name=workflow_name,
            mode=mode,
            started_at=started_at,
            steps=steps,
            outputs=outputs,
            warnings=warnings,
            assumptions=assumptions,
            artifact_paths=artifact_paths,
        )

    def _resolve_requested_steps(self, *, mode: str, stage: str) -> tuple[str, ...]:
        if mode == "research_only":
            return ("research",)
        if mode == "stage_only":
            return self.STAGE_ONLY_STEPS.get(stage, self.STAGE_ONLY_STEPS["research"])
        return self.FULL_PLAN_STEPS

    def _build_preflight(self, *, mode: str, stage: str, requested_steps: list[str]) -> dict[str, Any]:
        runs_root = self.repo_root / "state" / "runs"
        health_path = runs_root / "healthcheck.json"
        dynamic_candidate_path = runs_root / "candidate_inputs.dynamic.json"
        static_candidate_path = runs_root / "candidate_inputs.json"
        backtest_path = runs_root / "classic_multifactor" / "vnpy_cta_backtest_report.json"

        candidate_available = dynamic_candidate_path.exists() or static_candidate_path.exists()
        backtest_available = backtest_path.exists()
        requires_candidates = any(step_name in requested_steps for step_name in {"candidate_framework", "planning"})

        warnings: list[str] = []
        blocking_reasons: list[str] = []
        checks = {
            "healthcheck_cache": {
                "path": str(health_path),
                "exists": health_path.exists(),
                "required": False,
            },
            "candidate_inputs_dynamic": {
                "path": str(dynamic_candidate_path),
                "exists": dynamic_candidate_path.exists(),
                "required": False,
            },
            "candidate_inputs_static": {
                "path": str(static_candidate_path),
                "exists": static_candidate_path.exists(),
                "required": False,
            },
            "candidate_inputs_any": {
                "path": f"{dynamic_candidate_path} | {static_candidate_path}",
                "exists": candidate_available,
                "required": requires_candidates,
            },
            "backtest_report": {
                "path": str(backtest_path),
                "exists": backtest_available,
                "required": stage == "live",
            },
        }

        if not health_path.exists():
            warnings.append("No cached healthcheck artifact found; the workflow will use an offline placeholder unless remote checks are enabled.")
        if requires_candidates and not candidate_available:
            message = "No candidate input artifact was found; candidate framework and planning will fall back to an empty local universe."
            if mode == "stage_only" and stage in {"simulation", "live"}:
                blocking_reasons.append(
                    "Candidate inputs are required for stage_only simulation/live runs, but neither state/runs/candidate_inputs.dynamic.json nor state/runs/candidate_inputs.json exists."
                )
            else:
                warnings.append(message)
        if not backtest_available and stage in {"backtest", "simulation", "live"}:
            message = "No local vn.py backtest report found; backtest validation and readiness will stay in warning mode."
            if mode == "stage_only" and stage == "live":
                blocking_reasons.append(
                    "A local vn.py backtest report is required before a stage_only live readiness run can proceed."
                )
            else:
                warnings.append(message)

        status = "blocked" if blocking_reasons else ("warning" if warnings else "ok")
        if status == "blocked":
            message = "Preflight blocked the requested workflow because required local artifacts are missing."
        elif status == "warning":
            message = "Preflight completed with warnings; the workflow will continue in report-only mode."
        else:
            message = "Preflight completed and the requested workflow has the expected local inputs."

        all_warnings = list(dict.fromkeys([*blocking_reasons, *warnings]))
        return {
            "status": status,
            "message": message,
            "warnings": all_warnings,
            "blocking_reasons": blocking_reasons,
            "checks": checks,
        }

    def _collect_assumptions(
        self,
        *,
        plan_artifact: PlanningArtifact | None,
        research_artifact: PlanningArtifact | None,
    ) -> list[PlanAssumption]:
        if plan_artifact is not None:
            return list(plan_artifact.assumptions)
        if research_artifact is not None:
            return list(research_artifact.assumptions)
        return []

    def _finalize_workflow(
        self,
        *,
        workflow_name: str,
        mode: str,
        started_at: str,
        steps: list[WorkflowStepResult],
        outputs: list[str],
        warnings: list[str],
        assumptions: list[PlanAssumption],
        artifact_paths: dict[str, str],
    ) -> dict[str, Any]:
        serialized_steps = [json.loads(json.dumps(step, default=lambda value: value.__dict__)) for step in steps]
        workflow = {
            "workflow_name": workflow_name,
            "mode": mode,
            "started_at": started_at,
            "status": "blocked" if any(step.status == "blocked" for step in steps) else "ok",
            "steps": serialized_steps,
            "outputs": list(outputs),
            "warnings": list(dict.fromkeys(warnings)),
            "assumptions": [item.__dict__ for item in assumptions],
        }
        workflow_obj = WorkflowRunResult(
            workflow_name=workflow_name,
            mode=mode,
            started_at=started_at,
            status=workflow["status"],
            steps=[WorkflowStepResult(**step) for step in workflow["steps"]],
            outputs=list(outputs),
            warnings=workflow["warnings"],
            assumptions=[PlanAssumption(**item) for item in workflow["assumptions"]],
        )
        workflow_path = self.store.save_workflow_summary(workflow_obj, slug="beginner_quant")
        workflow["outputs"].append(str(workflow_path))
        workflow["workflow_report"] = str(workflow_path)
        workflow.update(artifact_paths)
        return workflow

    def _load_backtest_report(self) -> dict[str, Any]:
        path = self.repo_root / "state" / "runs" / "classic_multifactor" / "vnpy_cta_backtest_report.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_health_snapshot(self, *, mode: str) -> dict[str, Any]:
        path = self.repo_root / "state" / "runs" / "healthcheck.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.setdefault("source", "cached_healthcheck")
            payload.setdefault("path", str(path))
            payload.setdefault("alerts", payload.get("alerts", []))
            return payload
        if self.allow_remote_checks and mode not in {"plan", "research_only"}:
            payload = HealthcheckService(self.repo_root).run()
            payload["source"] = "active_healthcheck"
            payload["path"] = str(path)
            return payload
        return {
            "status": "unknown",
            "source": "offline_placeholder",
            "path": "",
            "alerts": [
                {
                    "level": "medium",
                    "message": "No cached healthcheck artifact found; remote probe was skipped in plan mode.",
                }
            ],
        }
        from services.evaluation_hub.models import WorkflowRunResult, WorkflowStepResult, PlanAssumption

        workflow_obj = WorkflowRunResult(
            workflow_name=workflow_name,
            mode=mode,
            started_at=started_at,
            status=workflow["status"],
            steps=[WorkflowStepResult(**step) for step in workflow["steps"]],
            outputs=outputs,
            warnings=workflow["warnings"],
            assumptions=[PlanAssumption(**item) for item in workflow["assumptions"]],
        )
        workflow_path = self.store.save_workflow_summary(workflow_obj, slug="beginner_quant")
        outputs.append(str(workflow_path))
        workflow["workflow_report"] = str(workflow_path)
        workflow["research_artifact"] = str(research_path)
        workflow["plan_artifact"] = str(plan_path)
        return workflow

    def _load_backtest_report(self) -> dict[str, Any]:
        path = self.repo_root / "state" / "runs" / "classic_multifactor" / "vnpy_cta_backtest_report.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))