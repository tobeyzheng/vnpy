from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.evaluation_hub import EvaluationHub
from vnpy_llm.base import beijing_now_isoformat
from services.evaluation_hub.artifact_store import ArtifactStore
from services.evaluation_hub.beginner_candidate_selector import BeginnerCandidateSelector
from services.evaluation_hub.beginner_research import BeginnerResearchService
from services.evaluation_hub.candidate_framework import BeginnerCandidateFramework
from services.evaluation_hub.capability_registry import CapabilityRegistry, CapabilityStageResolver
from services.evaluation_hub.doc_renderer import BeginnerExplanationRenderer
from services.evaluation_hub.models import PlanAssumption, PlanningArtifact, WorkflowRunResult, WorkflowStepResult
from services.evaluation_hub.plan_generator import BeginnerPlanGenerator
from services.evaluation_hub.readiness_gate import ReadinessGateService
from services.healthcheck import HealthcheckService
from services.strategy.candidate_preparation import CandidateInputPreparationService
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
        self.candidate_selector = BeginnerCandidateSelector(self.candidate_framework, self.hub)
        self.plan_generator = BeginnerPlanGenerator(self.hub)
        self.readiness_gate = ReadinessGateService()
        self.store = ArtifactStore(self.repo_root)
        self.candidate_preparation = CandidateInputPreparationService(self.repo_root)

    def run(
        self,
        *,
        workflow_name: str = "beginner_quant",
        mode: str = "plan",
        profile: dict[str, Any] | None = None,
        preferred_markets: list[str] | None = None,
        max_candidates: int = 5,
        stage: str = "research",
        prepare_candidates: bool = False,
        prepare_include_market_data: bool = False,
        prepare_knot_runtime: str = "auto",
    ) -> dict[str, Any]:
        started_at = beijing_now_isoformat()
        profile = dict(profile or {})
        requested_steps = list(self._resolve_requested_steps(mode=mode, stage=stage))
        steps: list[WorkflowStepResult] = []
        warnings: list[str] = []
        outputs: list[str] = []
        artifact_paths: dict[str, str] = {}

        if prepare_candidates:
            candidate_prepare_report = self.candidate_preparation.prepare(
                include_market_data=prepare_include_market_data,
                knot_runtime=prepare_knot_runtime,
            )
            candidate_prepare_step = self.store.build_step(
                step="candidate_prepare",
                status="ok",
                message="Prepared normalized candidate input artifacts before workflow evaluation.",
                outputs=[str(candidate_prepare_report.get("report_path"))],
                warnings=list(candidate_prepare_report.get("summary", {}).get("warnings", [])),
                meta={
                    "requires_confirmation": False,
                    "market_coverage": list(candidate_prepare_report.get("summary", {}).get("market_coverage", [])),
                    "written_targets": list(candidate_prepare_report.get("summary", {}).get("written_targets", [])),
                    "include_market_data": bool(candidate_prepare_report.get("summary", {}).get("include_market_data")),
                    "knot_runtime": str(candidate_prepare_report.get("summary", {}).get("knot_runtime") or "auto"),
                },
            )
            artifact_paths["candidate_prepare_report"] = str(candidate_prepare_report.get("report_path"))
            outputs.append(str(candidate_prepare_report.get("report_path")))
            self._append_step(steps=steps, warnings=warnings, step=candidate_prepare_step)

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
        self._append_step(steps=steps, warnings=warnings, step=preflight_step)

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
        self._append_step(steps=steps, warnings=warnings, step=health_step)

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
        self._append_step(steps=steps, warnings=warnings, step=capabilities_step)

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
            research_step = self.store.build_step(
                step="research",
                status="ok",
                message="Generated beginner research artifact and readable documents.",
                outputs=[str(research_path)],
                meta={"requires_confirmation": False},
            )
            self._append_step(steps=steps, warnings=warnings, step=research_step)

        observations = []
        candidate_artifact: PlanningArtifact | None = None
        if any(step_name in requested_steps for step_name in {"candidate_framework", "planning"}):
            candidates = UnifiedCandidateProvider(self.repo_root).load()
            if preferred_markets:
                filtered = [row for row in candidates if row.get("market") in set(preferred_markets)]
            else:
                filtered = candidates
            candidate_artifact = self.candidate_selector.build_candidate_artifact(
                rows=filtered,
                research_artifact=research_artifact,
                preferred_markets=preferred_markets,
                max_candidates=max_candidates,
            )
            observations = list(candidate_artifact.candidate_observations)
            if observations:
                candidate_artifact.capability_map = capability_map
                candidate_artifact.capability_gaps = capability_gaps
                candidate_artifact.rendered_documents = [
                    self.renderer.render_markdown(candidate_artifact),
                    self.renderer.render_json(candidate_artifact),
                ]
                candidate_path = self.store.save_artifact(candidate_artifact, slug="beginner_candidate_framework")
                artifact_paths["candidate_artifact"] = str(candidate_path)
                outputs.append(str(candidate_path))
            observation_summary = dict(candidate_artifact.meta.get("framework_summary") or self.candidate_framework.summary(observations))
            if "candidate_framework" in requested_steps:
                candidate_step = self.store.build_step(
                    step="candidate_framework",
                    status="ok" if observations else "warning",
                    message="Built enhanced beginner candidate observation list.",
                    outputs=[str(candidate_path)] if observations else [],
                    warnings=[] if observations else ["No candidate observations were produced from the current local inputs."],
                    meta={
                        "requires_confirmation": False,
                        "summary": observation_summary,
                        "next_actions": list(candidate_artifact.meta.get("next_step_suggestions") or []),
                        "llm_research_pending_count": observation_summary.get("llm_research_pending_count", 0),
                    },
                )
                self._append_step(steps=steps, warnings=warnings, step=candidate_step)

        backtest_report: dict[str, Any] = {}
        backtest_metadata: dict[str, Any] = {}
        backtest_checklist = None
        if any(step_name in requested_steps for step_name in {"backtest_validation", "planning"}):
            backtest_report = self._load_backtest_report()
            backtest_metadata = self.readiness_gate.normalize_backtest_report(backtest_report) if backtest_report else {}
            backtest_checklist = self.readiness_gate.validate_backtest_metadata(backtest_metadata) if backtest_metadata else None
            if "backtest_validation" in requested_steps:
                validation_warnings: list[str] = []
                if not backtest_report:
                    validation_warnings.append("No local vn.py backtest report was found for validation.")
                elif backtest_checklist is not None:
                    validation_warnings.extend(item.details for item in backtest_checklist.failed_items() if item.details)
                validation_step = self.store.build_step(
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
                self._append_step(steps=steps, warnings=warnings, step=validation_step)

        simulation_acceptance = self._collect_simulation_acceptance(stage=stage)
        live_evidence = self._collect_live_evidence(stage=stage)

        plan_artifact: PlanningArtifact | None = None
        if "planning" in requested_steps:
            previous_plan = self.store.load_previous_plan(slug="beginner_quant_plan")
            plan_artifact = self.plan_generator.build_plan(
                profile=profile,
                observations=observations,
                previous_plan=previous_plan,
            )
            plan_artifact.capability_map = capability_map
            plan_artifact.capability_gaps = capability_gaps
            readiness = self.readiness_gate.build_stage_checklist(
                stage=stage,
                health_status=str(health.get("status") or "ok"),
                has_research_artifact=research_artifact is not None,
                has_backtest_metadata=bool(backtest_checklist and backtest_checklist.passed),
                has_risk_budget=plan_artifact.risk_budget is not None,
                has_review_notes=True,
                capability_gaps=[gap.capability_id for gap in capability_gaps if gap.expected_path.startswith("scripts/run_hk")],
                simulation_acceptance=simulation_acceptance,
                live_evidence=live_evidence,
            )
            plan_artifact.readiness = readiness
            plan_artifact.meta.setdefault("profile", profile)
            plan_artifact.meta["current_stage"] = stage
            plan_artifact.meta["workflow_mode"] = mode
            plan_artifact.meta["previous_plan_loaded"] = previous_plan is not None
            plan_artifact.meta["simulation_acceptance"] = simulation_acceptance
            plan_artifact.meta["live_evidence"] = live_evidence
            if previous_plan is not None:
                plan_artifact.meta["previous_plan_generated_at"] = previous_plan.generated_at
                plan_artifact.meta["previous_plan_version"] = previous_plan.version
            plan_artifact.meta["next_step_suggestions"] = self._build_next_step_suggestions(
                artifact=plan_artifact,
                stage=stage,
            )
            plan_artifact.rendered_documents = [
                self.renderer.render_markdown(plan_artifact),
                self.renderer.render_json(plan_artifact),
            ]
            plan_path = self.store.save_artifact(plan_artifact, slug="beginner_quant_plan")
            artifact_paths["plan_artifact"] = str(plan_path)
            outputs.append(str(plan_path))
            block_upgrade, reasons = self.readiness_gate.should_block_upgrade(readiness)
            planning_step = self.store.build_step(
                step="planning",
                status="ok" if not block_upgrade else "blocked",
                message="Generated personal beginner plan and readiness checklist.",
                outputs=[str(plan_path)],
                warnings=reasons,
                meta={
                    "requires_confirmation": False,
                    "minimum_observation_requirements": self.readiness_gate.minimum_observation_requirements(stage),
                    "plan_differences": list(plan_artifact.meta.get("plan_differences") or []),
                    "personalization_summary": dict(plan_artifact.meta.get("personalization_summary") or {}),
                    "profile_update_scope": list(plan_artifact.meta.get("profile_update_scope") or []),
                    "previous_plan_loaded": previous_plan is not None,
                    "next_step_suggestions": list(plan_artifact.meta.get("next_step_suggestions") or []),
                    "readiness_failed_items": [item.name for item in readiness.failed_items()],
                    "simulation_acceptance": simulation_acceptance,
                    "live_evidence": live_evidence,
                },
            )
            self._append_step(steps=steps, warnings=warnings, step=planning_step)

        if "execution_boundary" in requested_steps:
            execution_caps = [cap for cap in capability_map if cap.stage in {"simulation", "live"}]
            if execution_caps:
                execution_step = self.store.build_step(
                    step="execution_boundary",
                    status="planned",
                    message="Execution-capable entries were detected but not run.",
                    warnings=[f"Confirmation required before using {cap.path}" for cap in execution_caps],
                    meta={
                        "requires_confirmation": True,
                        "capabilities": [cap.capability_id for cap in execution_caps],
                    },
                )
                self._append_step(steps=steps, warnings=warnings, step=execution_step)
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

    def _append_step(
        self,
        *,
        steps: list[WorkflowStepResult],
        warnings: list[str],
        step: WorkflowStepResult,
    ) -> None:
        steps.append(step)
        warnings.extend(step.warnings)

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

    def _collect_simulation_acceptance(self, *, stage: str) -> dict[str, Any]:
        reports_root = self.repo_root / "state" / "runs" / "reports"
        preflight_files = sorted(reports_root.glob("preflight_*.json")) if reports_root.exists() else []
        diff_files = sorted(reports_root.glob("*dual_run_diff*.json")) if reports_root.exists() else []

        latest_preflight_path = preflight_files[-1] if preflight_files else None
        latest_diff_path = diff_files[-1] if diff_files else None
        latest_preflight = self._safe_load_json(latest_preflight_path)
        latest_diff = self._safe_load_json(latest_diff_path)

        passed_preflights = [path for path in preflight_files if self._preflight_passed(self._safe_load_json(path))]
        passed_diffs = [path for path in diff_files if self._diff_report_passed(self._safe_load_json(path))]
        minimums = self.readiness_gate.minimum_observation_requirements(stage)

        return {
            "required_days": int(minimums.get("minimum_simulation_days") or 0),
            "passed_days": min(len(passed_preflights), len(passed_diffs)),
            "history_points": max(len(preflight_files), len(diff_files)),
            "latest_preflight_passed": self._preflight_passed(latest_preflight),
            "latest_diff_passed": self._diff_report_passed(latest_diff),
            "latest_preflight_path": str(latest_preflight_path) if latest_preflight_path else "",
            "latest_report_path": str(latest_diff_path) if latest_diff_path else "",
            "history_summary": [
                {
                    "preflight_reports": len(preflight_files),
                    "preflight_passed": len(passed_preflights),
                    "diff_reports": len(diff_files),
                    "diff_passed": len(passed_diffs),
                }
            ],
        }

    def _collect_live_evidence(self, *, stage: str) -> dict[str, Any]:
        live_report_paths = [
            self.repo_root / "state" / "runs" / "hk_live_task_report.json",
            self.repo_root / "state" / "runs" / "us_live_task_report.json",
        ]
        reconciliation_paths = [
            self.repo_root / "state" / "runs" / "futu_live_position_reconcile.json",
            self.repo_root / "state" / "runs" / "futu_sim_position_reconcile.json",
        ]
        report_path = next((path for path in live_report_paths if path.exists()), None)
        reconciliation_path = next((path for path in reconciliation_paths if path.exists()), None)
        report_payload = self._safe_load_json(report_path)

        approval_switches_documented = False
        if isinstance(report_payload, dict):
            approval_switches_documented = bool(
                report_payload.get("env_var_required")
                and (report_payload.get("risk_config") or {}).get("approval_env_var_required")
            )

        return {
            "report_schema_ready": report_path is not None,
            "approval_switches_documented": approval_switches_documented,
            "reconciliation_recent": reconciliation_path is not None,
            "risk_guard_auditable": bool(report_path and report_payload and isinstance(report_payload.get("risk_config"), dict)),
            "report_path": str(report_path) if report_path else "",
            "reconciliation_path": str(reconciliation_path) if reconciliation_path else "",
            "approval_notes": [
                "HK/US live wrappers preserve explicit --live-submit intent and downstream VNPY_LIVE_* switches."
            ] if stage == "live" else [],
        }

    def _safe_load_json(self, path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _preflight_passed(self, payload: dict[str, Any]) -> bool:
        totals = payload.get("totals") or {}
        return bool(payload) and int(totals.get("fail") or 0) == 0

    def _diff_report_passed(self, payload: dict[str, Any]) -> bool:
        totals = payload.get("totals") or {}
        business_keys = payload.get("business_keys") or {}
        return bool(payload) and int(totals.get("fail") or 0) == 0 and int(business_keys.get("only_a_total_count") or 0) == 0 and int(business_keys.get("only_b_total_count") or 0) == 0

    def _build_next_step_suggestions(self, *, artifact: PlanningArtifact, stage: str) -> list[str]:
        suggestions = list(artifact.execution_suggestions)
        if artifact.readiness and not artifact.readiness.passed:
            suggestions.append("Resolve readiness failures before upgrading to the next stage.")
        if artifact.capability_gaps:
            suggestions.append("Keep capability gaps visible and use manual alternatives where needed.")
        minimums = self.readiness_gate.minimum_observation_requirements(stage)
        if minimums.get("minimum_simulation_days", 0) > 0:
            suggestions.append(
                f"Stay in observation/simulation mode for at least {minimums['minimum_observation_days']} observation days and {minimums['minimum_simulation_days']} simulation days before stage upgrade."
            )
        else:
            suggestions.append(
                f"Maintain at least {minimums['minimum_observation_days']} observation days before changing the workflow scope."
            )
        return list(dict.fromkeys(suggestions))

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
        latest_index_path = self.store.root / "latest_index.json"
        workflow["latest_index"] = str(latest_index_path)
        workflow["workflow_summary"] = {
            "step_count": len(workflow["steps"]),
            "output_count": len(workflow["outputs"]),
            "warning_count": len(workflow["warnings"]),
            "blocked_steps": [step["step"] for step in workflow["steps"] if step["status"] == "blocked"],
            "confirmation_required_steps": [step["step"] for step in workflow["steps"] if step.get("meta", {}).get("requires_confirmation")],
        }
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