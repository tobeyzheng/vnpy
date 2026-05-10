from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from services.evaluation_hub import EvaluationHub
from scripts.classic_multifactor.cta_backtest import ClassicCtaBacktestRunner, build_opt_setting, dump_sweep_results
from scripts.classic_multifactor.data import VnpyBarRepository, parse_symbol
from vnpy_llm.base import beijing_now_isoformat
from services.evaluation_hub.artifact_store import ArtifactStore
from services.evaluation_hub.beginner_candidate_selector import BeginnerCandidateSelector
from services.evaluation_hub.candidate_framework import BeginnerCandidateFramework
from services.evaluation_hub.doc_renderer import BeginnerExplanationRenderer
from services.evaluation_hub.models import (
    PlanAssumption,
    PlanningArtifact,
    ReadinessCheckItem,
    ReadinessChecklist,
    ResearchFinding,
    WorkflowRunResult,
    WorkflowStepResult,
)
from services.healthcheck import HealthcheckService
from services.strategy.candidate_preparation import CandidateInputPreparationService
from services.strategy.candidate_provider import UnifiedCandidateProvider


class QuantWorkflowService:
    FULL_PLAN_STEPS = (
        "healthcheck",
        "candidate_framework",
        "backtest",
        "readiness",
    )
    STAGE_ONLY_STEPS = {
        "healthcheck": ("healthcheck",),
        "candidate_framework": ("healthcheck", "candidate_framework"),
        "backtest": ("healthcheck", "candidate_framework", "backtest"),
        "readiness": ("healthcheck", "candidate_framework", "backtest", "readiness"),
    }
    MARKET_ALIASES = {
        "hk": "hong_kong",
        "hongkong": "hong_kong",
        "hong_kong": "hong_kong",
        "us": "us",
    }

    def __init__(self, repo_root: Path, *, allow_remote_checks: bool = False):
        self.repo_root = Path(repo_root)
        self.allow_remote_checks = bool(allow_remote_checks)
        self.hub = EvaluationHub()
        self.renderer = BeginnerExplanationRenderer()
        self.candidate_framework = BeginnerCandidateFramework()
        self.candidate_selector = BeginnerCandidateSelector(self.candidate_framework, self.hub)
        self.store = ArtifactStore(self.repo_root)
        self.candidate_preparation = CandidateInputPreparationService(self.repo_root)

    def _normalize_market(self, market: Any) -> str:
        value = str(market or "").strip().lower()
        return self.MARKET_ALIASES.get(value, value)

    def _normalize_preferred_markets(self, preferred_markets: list[str] | None) -> list[str]:
        normalized = [self._normalize_market(market) for market in (preferred_markets or [])]
        return [market for market in dict.fromkeys(normalized) if market]

    def _is_backtest_target_candidate(self, observation: Any) -> bool:
        if not observation.meta.get("backtest_ready"):
            return False
        if observation.selected_as != "validate_only":
            return True
        return self._normalize_market(getattr(observation, "market", "")) == "hong_kong"

    def _backtest_target_reason(self, observation: Any) -> str:
        if not observation.meta.get("backtest_ready"):
            return "Trading cadence is still pending review."
        if observation.selected_as != "validate_only":
            return "Selected observation is promoted beyond validation-only and can enter backtest evidence review."
        if self._normalize_market(getattr(observation, "market", "")) == "hong_kong":
            return "Hong Kong validate-only names with a concrete daily/minute cadence still enter the evidence-only backtest stage."
        return "Validation-only names stay outside the backtest target set until they are promoted."

    def run(
        self,
        *,
        workflow_name: str = "beginner_quant",
        mode: str = "plan",
        profile: dict[str, Any] | None = None,
        preferred_markets: list[str] | None = None,
        max_candidates: int = 5,
        stage: str = "readiness",
        task_type: str = "simulation",
        prepare_candidates: bool = False,
        prepare_include_market_data: bool = False,
        prepare_knot_runtime: str = "auto",
        auto_execute_backtests: bool = False,
        backtest_optimize_mode: str = "ga",
        backtest_start: str | None = None,
        backtest_end: str | None = None,
        backtest_rate: float = 0.0003,
        backtest_slippage: float = 0.05,
        backtest_size: int = 1,
        backtest_pricetick: float = 0.01,
        backtest_top_n: int = 20,
        backtest_workers: int | None = None,
    ) -> dict[str, Any]:
        started_at = beijing_now_isoformat()
        profile = dict(profile or {})
        preferred_markets = self._normalize_preferred_markets(preferred_markets)
        requested_steps = list(self._resolve_requested_steps(mode=mode, stage=stage))
        steps: list[WorkflowStepResult] = []
        warnings: list[str] = []
        outputs: list[str] = []
        artifact_paths: dict[str, str] = {}

        if prepare_candidates:
            prepare_step, prepare_report_path = self._run_candidate_prepare(
                include_market_data=prepare_include_market_data,
                knot_runtime=prepare_knot_runtime,
            )
            if prepare_report_path:
                artifact_paths["candidate_prepare_report"] = prepare_report_path
                outputs.append(prepare_report_path)
            self._append_step(steps=steps, warnings=warnings, step=prepare_step)
            if prepare_step.status == "blocked":
                return self._finalize_workflow(
                    workflow_name=workflow_name,
                    mode=mode,
                    task_type=task_type,
                    started_at=started_at,
                    steps=steps,
                    outputs=outputs,
                    warnings=warnings,
                    assumptions=self._workflow_assumptions(profile=profile, preferred_markets=preferred_markets, task_type=task_type),
                    artifact_paths=artifact_paths,
                )

        health_summary = self._build_healthcheck(
            mode=mode,
            task_type=task_type,
            requested_steps=requested_steps,
            auto_execute_backtests=auto_execute_backtests,
        )
        health_step = self.store.build_step(
            step="healthcheck",
            status=health_summary["status"],
            message=health_summary["message"],
            outputs=list(health_summary["outputs"]),
            warnings=list(dict.fromkeys([*health_summary["blocking_reasons"], *health_summary["warnings"]])),
            meta={
                "requires_confirmation": False,
                "task_type": task_type,
                "checks": health_summary["checks"],
                "blocking_reasons": health_summary["blocking_reasons"],
                "health_status": health_summary["health_payload"].get("status"),
                "health_source": health_summary["health_payload"].get("source"),
            },
        )
        self._append_step(steps=steps, warnings=warnings, step=health_step)
        if health_step.status == "blocked":
            return self._finalize_workflow(
                workflow_name=workflow_name,
                mode=mode,
                task_type=task_type,
                started_at=started_at,
                steps=steps,
                outputs=outputs,
                warnings=warnings,
                assumptions=self._workflow_assumptions(profile=profile, preferred_markets=preferred_markets, task_type=task_type),
                artifact_paths=artifact_paths,
            )

        observations = []
        candidate_artifact: PlanningArtifact | None = None
        candidate_rows: list[dict[str, Any]] = []
        if "candidate_framework" in requested_steps or "backtest" in requested_steps or "readiness" in requested_steps:
            candidate_rows = UnifiedCandidateProvider(self.repo_root).load()
            if preferred_markets:
                preferred_market_set = set(preferred_markets)
                candidate_rows = [
                    row for row in candidate_rows if self._normalize_market(row.get("market")) in preferred_market_set
                ]
            candidate_artifact = self.candidate_selector.build_candidate_artifact(
                rows=candidate_rows,
                preferred_markets=preferred_markets,
                max_candidates=max(int(max_candidates), 1),
            )
            observations = list(candidate_artifact.candidate_observations)
            backtest_targets = [
                {
                    "symbol": item.symbol,
                    "market": item.market,
                    "selected_as": item.selected_as,
                    "trading_level": item.meta.get("trading_level"),
                    "backtest_ready": bool(item.meta.get("backtest_ready")),
                    "backtest_target_eligible": self._is_backtest_target_candidate(item),
                    "backtest_target_reason": self._backtest_target_reason(item),
                    "trading_level_reasons": list(item.meta.get("trading_level_reasons") or []),
                }
                for item in observations
            ]
            candidate_artifact.meta["profile"] = dict(profile)
            candidate_artifact.meta["task_type"] = task_type
            candidate_artifact.meta["backtest_targets"] = backtest_targets
            candidate_artifact.meta["preferred_markets"] = preferred_markets
            candidate_artifact.rendered_documents = [
                self.renderer.render_markdown(candidate_artifact),
                self.renderer.render_json(candidate_artifact),
            ]
            candidate_path = self.store.save_artifact(candidate_artifact, slug="beginner_quant_candidate_framework")
            artifact_paths["candidate_artifact"] = str(candidate_path)
            outputs.append(str(candidate_path))
            if "candidate_framework" in requested_steps:
                summary = dict(candidate_artifact.meta.get("framework_summary") or self.candidate_framework.summary(observations))
                candidate_status = "ok" if observations else "warning"
                candidate_step = self.store.build_step(
                    step="candidate_framework",
                    status=candidate_status,
                    message="Selected observation targets and assigned daily/minute readiness levels from local candidate inputs.",
                    outputs=[str(candidate_path)],
                    warnings=[] if observations else ["No candidate observations were produced from the current local inputs."],
                    meta={
                        "requires_confirmation": False,
                        "summary": summary,
                        "backtest_targets": backtest_targets,
                    },
                )
                self._append_step(steps=steps, warnings=warnings, step=candidate_step)

        backtest_entries: list[dict[str, Any]] = []
        backtest_artifact: PlanningArtifact | None = None
        if "backtest" in requested_steps or "readiness" in requested_steps:
            backtest_artifact = self._build_backtest_artifact(
                observations=observations,
                profile=profile,
                preferred_markets=preferred_markets,
                task_type=task_type,
                auto_execute_backtests=auto_execute_backtests,
                optimize_mode=backtest_optimize_mode,
                backtest_start=backtest_start,
                backtest_end=backtest_end,
                backtest_rate=backtest_rate,
                backtest_slippage=backtest_slippage,
                backtest_size=backtest_size,
                backtest_pricetick=backtest_pricetick,
                backtest_top_n=backtest_top_n,
                backtest_workers=backtest_workers,
            )
            backtest_entries = list(backtest_artifact.meta.get("backtest_results") or [])
            backtest_path = self.store.save_artifact(backtest_artifact, slug="beginner_quant_backtest")
            artifact_paths["backtest_artifact"] = str(backtest_path)
            outputs.append(str(backtest_path))
            if "backtest" in requested_steps:
                summary = dict(backtest_artifact.meta.get("backtest_summary") or {})
                backtest_status = str(summary.get("status") or "warning")
                backtest_step = self.store.build_step(
                    step="backtest",
                    status=backtest_status,
                    message="Collected historical backtest and optimization evidence for the observation targets.",
                    outputs=[str(backtest_path)],
                    warnings=list(backtest_artifact.meta.get("backtest_warnings") or []),
                    meta={
                        "requires_confirmation": False,
                        "summary": summary,
                        "results": backtest_entries,
                    },
                )
                self._append_step(steps=steps, warnings=warnings, step=backtest_step)

        if "readiness" in requested_steps:
            readiness_artifact = self._build_readiness_artifact(
                task_type=task_type,
                health_summary=health_summary,
                observations=observations,
                backtest_entries=backtest_entries,
                profile=profile,
                preferred_markets=preferred_markets,
            )
            readiness_path = self.store.save_artifact(readiness_artifact, slug="beginner_quant_readiness")
            artifact_paths["readiness_artifact"] = str(readiness_path)
            outputs.append(str(readiness_path))
            readiness = readiness_artifact.readiness
            readiness_status = "ok"
            if readiness is not None and readiness.failed_items():
                readiness_status = "blocked" if any(item.severity in {"high", "critical"} for item in readiness.failed_items()) else "warning"
            readiness_step = self.store.build_step(
                step="readiness",
                status=readiness_status,
                message="Evaluated whether the current health, candidate, and backtest evidence is sufficient for the requested next stage.",
                outputs=[str(readiness_path)],
                warnings=[item.details for item in (readiness.failed_items() if readiness else []) if item.details],
                meta={
                    "requires_confirmation": False,
                    "task_type": task_type,
                    "failed_items": [item.name for item in (readiness.failed_items() if readiness else [])],
                    "next_step_suggestions": list(readiness_artifact.meta.get("next_step_suggestions") or []),
                    "simulation_acceptance": readiness_artifact.meta.get("simulation_acceptance", {}),
                    "live_evidence": readiness_artifact.meta.get("live_evidence", {}),
                },
            )
            self._append_step(steps=steps, warnings=warnings, step=readiness_step)

        return self._finalize_workflow(
            workflow_name=workflow_name,
            mode=mode,
            task_type=task_type,
            started_at=started_at,
            steps=steps,
            outputs=outputs,
            warnings=warnings,
            assumptions=self._workflow_assumptions(profile=profile, preferred_markets=preferred_markets, task_type=task_type),
            artifact_paths=artifact_paths,
        )

    def _run_candidate_prepare(self, *, include_market_data: bool, knot_runtime: str) -> tuple[WorkflowStepResult, str]:
        report = self.candidate_preparation.prepare(
            include_market_data=include_market_data,
            knot_runtime=knot_runtime,
        )
        summary = dict(report.get("summary") or {})
        written_targets = list(summary.get("written_targets") or [])
        total_items = int(summary.get("total_items") or 0)
        warnings = list(summary.get("warnings") or [])
        blocking_reasons: list[str] = []
        if not written_targets:
            blocking_reasons.append("Candidate preparation did not write any normalized candidate target.")
        if total_items <= 0:
            blocking_reasons.append("Candidate preparation completed without any candidate rows, so downstream workflow stages cannot continue.")
        status = "blocked" if blocking_reasons else ("warning" if warnings else "ok")
        step = self.store.build_step(
            step="candidate_prepare",
            status=status,
            message="Prepared normalized candidate inputs before workflow evaluation." if not blocking_reasons else "Candidate input preparation is incomplete for the requested workflow.",
            outputs=[str(report.get("report_path"))] if report.get("report_path") else [],
            warnings=list(dict.fromkeys([*blocking_reasons, *warnings])),
            meta={
                "requires_confirmation": False,
                "market_coverage": list(summary.get("market_coverage") or []),
                "written_targets": written_targets,
                "include_market_data": bool(summary.get("include_market_data")),
                "knot_runtime": str(summary.get("knot_runtime") or "auto"),
                "generated_at": str(report.get("generated_at") or ""),
                "missing_required_field_counts": dict(summary.get("missing_required_field_counts") or {}),
            },
        )
        return step, str(report.get("report_path") or "")

    def _append_step(self, *, steps: list[WorkflowStepResult], warnings: list[str], step: WorkflowStepResult) -> None:
        steps.append(step)
        warnings.extend(step.warnings)

    def _resolve_requested_steps(self, *, mode: str, stage: str) -> tuple[str, ...]:
        if mode == "healthcheck_only":
            return ("healthcheck",)
        if mode == "stage_only":
            return self.STAGE_ONLY_STEPS.get(stage, self.STAGE_ONLY_STEPS["readiness"])
        return self.FULL_PLAN_STEPS

    def _build_healthcheck(
        self,
        *,
        mode: str,
        task_type: str,
        requested_steps: list[str],
        auto_execute_backtests: bool = False,
    ) -> dict[str, Any]:
        runs_root = self.repo_root / "state" / "runs"
        health_payload = self._load_health_snapshot(mode=mode)
        health_path = str(health_payload.get("path") or "")
        checks: dict[str, dict[str, Any]] = {}
        outputs: list[str] = []
        warnings: list[str] = []
        blocking_reasons: list[str] = []

        def add_check(name: str, *, path: str, exists: bool, required: bool, note: str) -> None:
            checks[name] = {
                "path": path,
                "exists": bool(exists),
                "required": bool(required),
                "note": note,
            }
            if exists and path:
                outputs.append(path)
            if required and not exists:
                blocking_reasons.append(f"Missing required healthcheck evidence: {name} -> {path}.")

        requires_candidates = any(step in requested_steps for step in {"candidate_framework", "backtest", "readiness"})
        requires_backtest = any(step in requested_steps for step in {"backtest", "readiness"})
        requires_readiness = "readiness" in requested_steps

        dynamic_candidate_path = runs_root / "candidate_inputs.dynamic.json"
        static_candidate_path = runs_root / "candidate_inputs.json"
        backtest_path = runs_root / "classic_multifactor" / "vnpy_cta_backtest_report.json"
        sweep_path = runs_root / "classic_multifactor" / "vnpy_cta_sweep_report.json"
        sim_session_path = runs_root / "hk_futu_sim_session_report.json"
        sim_reconcile_path = runs_root / "futu_sim_position_reconcile.json"
        live_report_path = next(
            (path for path in (runs_root / "hk_live_task_report.json", runs_root / "us_live_task_report.json") if path.exists()),
            runs_root / "hk_live_task_report.json",
        )
        live_reconcile_path = runs_root / "futu_live_position_reconcile.json"
        reports_root = runs_root / "reports"
        latest_preflight = self._latest_matching_path(reports_root, "preflight_*.json")
        latest_diff = self._latest_matching_path(reports_root, "*dual_run_diff*.json")

        add_check(
            "health_snapshot",
            path=health_path,
            exists=bool(health_path),
            required=False,
            note="Cached or active environment health snapshot.",
        )
        add_check(
            "candidate_inputs_dynamic",
            path=str(dynamic_candidate_path),
            exists=dynamic_candidate_path.exists(),
            required=False,
            note="Prepared dynamic candidate input artifact.",
        )
        add_check(
            "candidate_inputs_static",
            path=str(static_candidate_path),
            exists=static_candidate_path.exists(),
            required=False,
            note="Prepared static candidate input artifact.",
        )
        add_check(
            "candidate_inputs_any",
            path=f"{dynamic_candidate_path} | {static_candidate_path}",
            exists=dynamic_candidate_path.exists() or static_candidate_path.exists(),
            required=requires_candidates,
            note="Any local candidate input artifact required by candidate/backtest/readiness stages.",
        )
        add_check(
            "backtest_report",
            path=str(backtest_path),
            exists=backtest_path.exists(),
            required=bool(requires_backtest and not auto_execute_backtests),
            note="Local historical backtest report evidence.",
        )
        add_check(
            "optimization_report",
            path=str(sweep_path),
            exists=sweep_path.exists(),
            required=False,
            note="Local optimization sweep report used to recover best parameters.",
        )
        add_check(
            "sim_session_report",
            path=str(sim_session_path),
            exists=sim_session_path.exists(),
            required=False,
            note="Latest simulation session report if simulation evidence has already been collected.",
        )
        add_check(
            "sim_reconciliation",
            path=str(sim_reconcile_path),
            exists=sim_reconcile_path.exists(),
            required=False,
            note="Latest simulation reconciliation artifact.",
        )
        add_check(
            "simulation_preflight",
            path=str(latest_preflight) if latest_preflight else str(reports_root / "preflight_<date>.json"),
            exists=latest_preflight is not None,
            required=bool(requires_readiness and task_type == "live"),
            note="Latest simulation preflight pass/fail evidence.",
        )
        add_check(
            "simulation_diff",
            path=str(latest_diff) if latest_diff else str(reports_root / "dual_run_diff_<date>.json"),
            exists=latest_diff is not None,
            required=bool(requires_readiness and task_type == "live"),
            note="Latest simulation dual-run or reconciliation diff evidence.",
        )
        add_check(
            "live_report",
            path=str(live_report_path),
            exists=live_report_path.exists(),
            required=bool(requires_readiness and task_type == "live"),
            note="Latest live-task report or audit schema evidence.",
        )
        add_check(
            "live_reconciliation",
            path=str(live_reconcile_path),
            exists=live_reconcile_path.exists(),
            required=bool(requires_readiness and task_type == "live"),
            note="Latest live reconciliation evidence.",
        )

        alerts = [alert.get("message", "") for alert in health_payload.get("alerts", []) if alert.get("message")]
        warnings.extend(alerts)
        if health_payload.get("status") == "blocked":
            blocking_reasons.append("Cached or active health status is blocked.")
        elif health_payload.get("status") == "unknown":
            warnings.append("No cached healthcheck artifact found; the workflow is using an offline placeholder.")

        if requires_backtest and auto_execute_backtests and not backtest_path.exists():
            warnings.append("No cached backtest report was found; the workflow will attempt to generate real backtest evidence during the backtest stage.")

        if task_type == "simulation":
            if requires_readiness and not sim_session_path.exists():
                warnings.append("No simulation session report was found yet; simulation readiness can still proceed, but local session evidence remains incomplete.")
            if requires_readiness and not sim_reconcile_path.exists():
                warnings.append("No simulation reconciliation artifact was found yet; review the first simulation run before upgrading further.")

        status = "blocked" if blocking_reasons else ("warning" if warnings else "ok")
        if status == "blocked":
            message = "Unified healthcheck blocked the requested workflow because required local evidence is missing."
        elif status == "warning":
            message = "Unified healthcheck completed with warnings; downstream stages may continue in evidence-first mode."
        else:
            message = "Unified healthcheck completed and the requested local evidence is available."

        return {
            "status": status,
            "message": message,
            "warnings": list(dict.fromkeys(warnings)),
            "blocking_reasons": list(dict.fromkeys(blocking_reasons)),
            "checks": checks,
            "outputs": list(dict.fromkeys(outputs)),
            "health_payload": health_payload,
        }

    def _build_backtest_artifact(
        self,
        *,
        observations: list[Any],
        profile: dict[str, Any],
        preferred_markets: list[str],
        task_type: str,
        auto_execute_backtests: bool = False,
        optimize_mode: str = "ga",
        backtest_start: str | None = None,
        backtest_end: str | None = None,
        backtest_rate: float = 0.0003,
        backtest_slippage: float = 0.05,
        backtest_size: int = 1,
        backtest_pricetick: float = 0.01,
        backtest_top_n: int = 20,
        backtest_workers: int | None = None,
    ) -> PlanningArtifact:
        backtest_targets = [item for item in observations if self._is_backtest_target_candidate(item)]
        entries = [
            self._collect_backtest_entry(
                item,
                profile=profile,
                auto_execute_backtests=auto_execute_backtests,
                optimize_mode=optimize_mode,
                backtest_start=backtest_start,
                backtest_end=backtest_end,
                backtest_rate=backtest_rate,
                backtest_slippage=backtest_slippage,
                backtest_size=backtest_size,
                backtest_pricetick=backtest_pricetick,
                backtest_top_n=backtest_top_n,
                backtest_workers=backtest_workers,
            )
            for item in backtest_targets
        ]
        ok_count = sum(1 for item in entries if item.get("status") == "ok")
        missing_count = sum(1 for item in entries if item.get("status") != "ok")
        optimized_count = sum(1 for item in entries if item.get("best_params"))
        warnings = [warning for item in entries for warning in item.get("warnings", [])]
        if not backtest_targets:
            warnings.append("No observation target is currently marked as backtest-ready.")
        status = "ok" if ok_count > 0 and missing_count == 0 else ("warning" if ok_count > 0 else "blocked")
        evidence_mode = "vnpy_automated_execution" if auto_execute_backtests else "local_artifact_reuse"
        summary = {
            "status": status,
            "task_type": task_type,
            "target_count": len(backtest_targets),
            "ok_count": ok_count,
            "missing_count": missing_count,
            "optimized_count": optimized_count,
            "daily_count": sum(1 for item in entries if item.get("trading_level") == "daily"),
            "minute_count": sum(1 for item in entries if item.get("trading_level") == "minute"),
            "execution_mode": evidence_mode,
            "executed_count": sum(1 for item in entries if item.get("execution_mode") == "executed"),
        }
        findings = [
            ResearchFinding(
                topic="workflow_backtest_summary",
                conclusion=(
                    f"Collected backtest evidence for {len(backtest_targets)} observation target(s): "
                    f"{ok_count} with report evidence, {optimized_count} with optimization evidence, and {missing_count} still missing local backtest artifacts."
                ),
                evidence_level="mid" if ok_count else "low",
                source_kind="local_backtest_artifacts",
                confidence=0.78 if ok_count else 0.42,
                verification_status="verified",
            )
        ]
        for item in entries:
            if item.get("status") == "ok":
                findings.append(
                    ResearchFinding(
                        topic=item["symbol"],
                        conclusion=(
                            f"{item['symbol']} has {item['trading_level']} backtest evidence with interval {item['preferred_interval']} "
                            f"and optimization status {item.get('optimization_status')}."
                        ),
                        evidence_level="mid" if item.get("best_params") else "low",
                        source_kind="local_backtest_artifacts",
                        confidence=0.72 if item.get("best_params") else 0.58,
                        verification_status="verified",
                    )
                )
        artifact = self.hub.build_planning_artifact(
            artifact_type="beginner_quant_backtest",
            title="Quant Workflow Backtest Evidence",
            generated_at=beijing_now_isoformat(),
            assumptions=self._workflow_assumptions(profile=profile, preferred_markets=preferred_markets, task_type=task_type),
            research_findings=findings,
            research_conclusions=[finding.conclusion for finding in findings],
            execution_suggestions=self._backtest_suggestions(entries),
            risk_prompts=list(dict.fromkeys(warnings))[:10],
            invalidation_conditions=[
                "If a candidate loses its trading_level assignment or local report path changes, regenerate the backtest evidence artifact.",
                "If optimization evidence is stale or missing, do not treat the candidate as fully backtest-ready for stage upgrade.",
            ],
            candidate_observations=list(backtest_targets),
            meta={
                "profile": dict(profile),
                "task_type": task_type,
                "backtest_results": entries,
                "backtest_summary": summary,
                "backtest_warnings": list(dict.fromkeys(warnings)),
                "preferred_markets": list(preferred_markets),
                "evidence_mode": evidence_mode,
                "next_step_suggestions": self._backtest_suggestions(entries),
            },
        )
        artifact.rendered_documents = [
            self.renderer.render_markdown(artifact),
            self.renderer.render_json(artifact),
        ]
        return artifact

    def _collect_backtest_entry(
        self,
        observation: Any,
        *,
        profile: dict[str, Any],
        auto_execute_backtests: bool,
        optimize_mode: str,
        backtest_start: str | None,
        backtest_end: str | None,
        backtest_rate: float,
        backtest_slippage: float,
        backtest_size: int,
        backtest_pricetick: float,
        backtest_top_n: int,
        backtest_workers: int | None,
    ) -> dict[str, Any]:
        trading_level = str(observation.meta.get("trading_level") or "needs_review")
        preferred_interval = "1m" if trading_level == "minute" else "1d"
        search_space = self._default_search_space(trading_level)
        report_payload, report_path = self._find_backtest_report(observation.symbol)
        optimization_payload, optimization_path, optimization_entry = self._find_optimization_payload(observation.symbol)
        execution_meta: dict[str, Any] = {"execution_mode": "reused"}
        if auto_execute_backtests and (not report_payload or not optimization_entry):
            execution_meta = self._execute_real_backtest(
                observation,
                profile=profile,
                preferred_interval=preferred_interval,
                search_space=search_space,
                optimize_mode=optimize_mode,
                backtest_start=backtest_start,
                backtest_end=backtest_end,
                backtest_rate=backtest_rate,
                backtest_slippage=backtest_slippage,
                backtest_size=backtest_size,
                backtest_pricetick=backtest_pricetick,
                backtest_top_n=backtest_top_n,
                backtest_workers=backtest_workers,
            )
            report_payload, report_path = self._find_backtest_report(observation.symbol)
            optimization_payload, optimization_path, optimization_entry = self._find_optimization_payload(observation.symbol)
        metrics = self._extract_backtest_metrics(report_payload)
        sample_period = {
            "start": report_payload.get("start") or metrics.get("start_date") or metrics.get("start") or "",
            "end": report_payload.get("end") or metrics.get("end_date") or metrics.get("end") or "",
        }
        best_params = dict(optimization_entry.get("params") or {}) if optimization_entry else {}
        warnings: list[str] = list(execution_meta.get("warnings") or [])
        if not report_payload:
            warnings.append(f"No local backtest report was found for {observation.symbol}.")
        if report_payload and not best_params:
            warnings.append(f"No optimization result was found for {observation.symbol}; only raw backtest evidence is available.")
        return {
            "symbol": observation.symbol,
            "market": observation.market,
            "selected_as": observation.selected_as,
            "trading_level": trading_level,
            "preferred_interval": preferred_interval,
            "search_space": search_space,
            "status": "ok" if report_payload else "missing",
            "backtest_report_path": report_path,
            "optimization_report_path": optimization_path,
            "optimization_status": "ok" if best_params else "missing",
            "best_params": best_params,
            "performance": metrics,
            "sample_period": sample_period,
            "warnings": warnings,
            "execution_mode": execution_meta.get("execution_mode") or "reused",
            "execution_start": execution_meta.get("execution_start") or sample_period.get("start") or "",
            "execution_end": execution_meta.get("execution_end") or sample_period.get("end") or "",
            "execution_errors": list(execution_meta.get("errors") or []),
            "capital": execution_meta.get("capital"),
            "rate": execution_meta.get("rate", backtest_rate),
            "slippage": execution_meta.get("slippage", backtest_slippage),
            "size": execution_meta.get("size", backtest_size),
            "pricetick": execution_meta.get("pricetick", backtest_pricetick),
            "optimize_mode": execution_meta.get("optimize_mode") or optimize_mode,
        }

    def _execute_real_backtest(
        self,
        observation: Any,
        *,
        profile: dict[str, Any],
        preferred_interval: str,
        search_space: dict[str, list[Any]],
        optimize_mode: str,
        backtest_start: str | None,
        backtest_end: str | None,
        backtest_rate: float,
        backtest_slippage: float,
        backtest_size: int,
        backtest_pricetick: float,
        backtest_top_n: int,
        backtest_workers: int | None,
    ) -> dict[str, Any]:
        symbol = str(observation.symbol)
        runtime = self._resolve_backtest_runtime(
            observation,
            profile=profile,
            preferred_interval=preferred_interval,
            backtest_start=backtest_start,
            backtest_end=backtest_end,
            backtest_rate=backtest_rate,
            backtest_slippage=backtest_slippage,
            backtest_size=backtest_size,
            backtest_pricetick=backtest_pricetick,
        )
        result: dict[str, Any] = {
            "execution_mode": "executed",
            "execution_start": runtime["start"].date().isoformat(),
            "execution_end": runtime["end"].date().isoformat(),
            "warnings": [],
            "errors": [],
            "capital": runtime["capital"],
            "rate": runtime["rate"],
            "slippage": runtime["slippage"],
            "size": runtime["size"],
            "pricetick": runtime["pricetick"],
            "optimize_mode": optimize_mode,
        }
        try:
            VnpyBarRepository(fetch_futu_history=True).load_bars(
                symbol,
                runtime["start"],
                runtime["end"],
                preferred_interval,
            )
            runner = ClassicCtaBacktestRunner()
            setting = self._default_backtest_setting(observation, runtime["capital"], preferred_interval)
            stats, _engine = runner.run(
                vt_symbol=runtime["vt_symbol"],
                interval=preferred_interval,
                start=runtime["start"],
                end=runtime["end"],
                capital=runtime["capital"],
                rate=runtime["rate"],
                slippage=runtime["slippage"],
                size=runtime["size"],
                pricetick=runtime["pricetick"],
                setting=setting,
            )
            backtest_path = self._write_backtest_report(
                symbol=symbol,
                vt_symbol=runtime["vt_symbol"],
                interval=preferred_interval,
                start=runtime["start"],
                end=runtime["end"],
                setting=setting,
                stats=stats,
            )
            result["backtest_report_path"] = str(backtest_path)
            if stats.get("status") != "ok":
                result["warnings"].append(
                    f"Real backtest for {symbol} finished without an ok status: {stats.get('status') or 'unknown'}."
                )
                return result
            opt_specs = self._build_opt_param_specs(search_space)
            opt_setting = build_opt_setting("sharpe_ratio", opt_specs)
            sweep_results = runner.run_optimization(
                vt_symbol=runtime["vt_symbol"],
                interval=preferred_interval,
                start=runtime["start"],
                end=runtime["end"],
                capital=runtime["capital"],
                rate=runtime["rate"],
                slippage=runtime["slippage"],
                size=runtime["size"],
                pricetick=runtime["pricetick"],
                base_setting=setting,
                opt_setting=opt_setting,
                mode=optimize_mode,
                max_workers=backtest_workers,
                ga_kwargs={},
            )
            sweep_path = self._write_sweep_report(
                symbol=symbol,
                vt_symbol=runtime["vt_symbol"],
                interval=preferred_interval,
                start=runtime["start"],
                end=runtime["end"],
                base_setting=setting,
                search_space=search_space,
                results=sweep_results,
                optimize_mode=optimize_mode,
                top_n=backtest_top_n,
            )
            result["optimization_report_path"] = str(sweep_path)
            if not sweep_results:
                result["warnings"].append(f"Optimization for {symbol} completed without any ranked parameter result.")
        except Exception as exc:
            result["errors"].append(str(exc))
            result["warnings"].append(f"Real backtest execution failed for {symbol}: {exc}")
        return result

    def _resolve_backtest_runtime(
        self,
        observation: Any,
        *,
        profile: dict[str, Any],
        preferred_interval: str,
        backtest_start: str | None,
        backtest_end: str | None,
        backtest_rate: float,
        backtest_slippage: float,
        backtest_size: int,
        backtest_pricetick: float,
    ) -> dict[str, Any]:
        _market, _input_form, vt_symbol, _futu_code = parse_symbol(str(observation.symbol))
        end_text = str(backtest_end or datetime.now().date().isoformat())
        start_text = str(backtest_start or ((datetime.fromisoformat(end_text) - timedelta(days=365)).date().isoformat()))
        return {
            "vt_symbol": vt_symbol,
            "start": datetime.fromisoformat(start_text),
            "end": datetime.fromisoformat(end_text),
            "capital": float(profile.get("capital") or 20000.0),
            "rate": float(backtest_rate),
            "slippage": float(backtest_slippage),
            "size": int(backtest_size),
            "pricetick": float(backtest_pricetick),
        }

    def _default_backtest_setting(self, observation: Any, capital: float, preferred_interval: str) -> dict[str, Any]:
        setting: dict[str, Any] = {
            "capital": float(capital),
            "max_order_value": min(float(capital) * 0.35, 5000.0),
        }
        if preferred_interval == "1m":
            setting.update(
                {
                    "signal_interval_minutes": 5,
                    "entry_score": 0.64,
                    "max_intraday_trades": 4,
                }
            )
        else:
            setting.update(
                {
                    "fast_window": 10,
                    "slow_window": 60,
                    "momentum_window": 20,
                    "atr_window": 14,
                }
            )
        raw_score = observation.meta.get("raw_score")
        if raw_score is None:
            raw_score = getattr(observation, "raw_score", 0.0)
        if raw_score is not None:
            setting["raw_score"] = float(raw_score)
        return setting

    def _build_opt_param_specs(self, search_space: dict[str, list[Any]]) -> list[str]:
        specs: list[str] = []
        for key, values in search_space.items():
            if not values:
                continue
            if len(values) == 1:
                specs.append(f"{key}={values[0]}")
                continue
            start = values[0]
            end = values[-1]
            step = values[1] - values[0] if len(values) > 1 else 1
            specs.append(f"{key}={start}:{end}:{step}")
        return specs

    def _write_backtest_report(
        self,
        *,
        symbol: str,
        vt_symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        setting: dict[str, Any],
        stats: dict[str, Any],
    ) -> Path:
        root = self.repo_root / "state" / "runs" / "classic_multifactor"
        root.mkdir(parents=True, exist_ok=True)
        safe_symbol = symbol.replace(".", "_").replace("/", "_")
        symbol_path = root / f"vnpy_cta_backtest_{safe_symbol}.json"
        latest_path = root / "vnpy_cta_backtest_report.json"
        report = {
            "strategy": "classic_multifactor_no_llm_vnpy_cta",
            "symbol": symbol,
            "vt_symbol": vt_symbol,
            "interval": interval,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "setting": setting,
            "stats": stats,
        }
        payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        symbol_path.write_text(payload, encoding="utf-8")
        latest_path.write_text(payload, encoding="utf-8")
        return symbol_path

    def _write_sweep_report(
        self,
        *,
        symbol: str,
        vt_symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        base_setting: dict[str, Any],
        search_space: dict[str, list[Any]],
        results: list[tuple],
        optimize_mode: str,
        top_n: int,
    ) -> Path:
        root = self.repo_root / "state" / "runs" / "classic_multifactor"
        root.mkdir(parents=True, exist_ok=True)
        safe_symbol = symbol.replace(".", "_").replace("/", "_")
        symbol_path = root / f"vnpy_cta_sweep_{safe_symbol}.json"
        latest_path = root / "vnpy_cta_sweep_report.json"
        dump_sweep_results(
            results=results,
            output_path=symbol_path,
            mode=optimize_mode,
            target="sharpe_ratio",
            symbol=symbol,
            vt_symbol=vt_symbol,
            interval=interval,
            start=start,
            end=end,
            base_setting=base_setting,
            sweep_space=search_space,
            top_n=int(top_n),
        )
        latest_path.write_text(symbol_path.read_text(encoding="utf-8"), encoding="utf-8")
        return symbol_path

    def _find_backtest_report(self, symbol: str) -> tuple[dict[str, Any], str]:
        root = self.repo_root / "state" / "runs" / "classic_multifactor"
        if not root.exists():
            return {}, ""
        exact_payload: dict[str, Any] = {}
        exact_path = ""
        fallback_payload: dict[str, Any] = {}
        fallback_path = ""
        normalized_symbol = symbol.upper()
        for path in sorted(root.glob("*backtest*.json")):
            payload = self._safe_load_json(path)
            if not payload:
                continue
            payload_symbol = str(payload.get("symbol") or "").upper()
            if payload_symbol == normalized_symbol:
                return payload, str(path)
            if not fallback_payload and path.name == "vnpy_cta_backtest_report.json":
                fallback_payload = payload
                fallback_path = str(path)
            if not exact_payload:
                exact_payload = payload
                exact_path = str(path)
        return fallback_payload or exact_payload, fallback_path or exact_path

    def _find_optimization_payload(self, symbol: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
        root = self.repo_root / "state" / "runs" / "classic_multifactor"
        if not root.exists():
            return {}, "", {}
        normalized_symbol = symbol.upper()
        for path in sorted(root.glob("*sweep*.json")):
            payload = self._safe_load_json(path)
            if not payload:
                continue
            if str(payload.get("symbol") or "").upper() == normalized_symbol:
                best_items = list(payload.get("per_symbol_best") or [])
                if best_items:
                    return payload, str(path), dict(best_items[0])
            best_items = list(payload.get("per_symbol_best") or [])
            for item in best_items:
                if str(item.get("symbol") or "").upper() == normalized_symbol:
                    return payload, str(path), dict(item)
            ranking = list(payload.get("global_ranking") or [])
            for item in ranking:
                if str(item.get("symbol") or "").upper() == normalized_symbol:
                    return payload, str(path), dict(item)
        return {}, "", {}

    def _extract_backtest_metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        stats = dict(payload.get("stats") or payload.get("stats_summary") or {})
        metrics = {
            "status": stats.get("status") or payload.get("status") or "",
            "sharpe_ratio": stats.get("sharpe_ratio"),
            "return_drawdown_ratio": stats.get("return_drawdown_ratio"),
            "max_drawdown_pct": stats.get("max_drawdown_pct") or stats.get("max_ddpercent"),
            "total_return_pct": stats.get("total_return_pct") or stats.get("total_return"),
            "trade_count": stats.get("trade_count") or stats.get("total_trade_count") or stats.get("trade_count_runtime"),
            "sample_count": payload.get("bar_count") or stats.get("sample_count") or stats.get("total_days"),
            "start_date": stats.get("start_date") or payload.get("start"),
            "end_date": stats.get("end_date") or payload.get("end"),
        }
        return {key: value for key, value in metrics.items() if value not in {None, ""}}

    def _default_search_space(self, trading_level: str) -> dict[str, list[Any]]:
        if trading_level == "minute":
            return {
                "signal_interval_minutes": [3, 5, 10],
                "entry_score": [0.62, 0.64, 0.66, 0.68],
                "max_intraday_trades": [2, 4, 6],
            }
        return {
            "fast_window": [5, 10, 20],
            "slow_window": [30, 60, 90],
            "momentum_window": [10, 20, 30],
            "atr_window": [10, 14, 20],
        }

    def _backtest_suggestions(self, entries: list[dict[str, Any]]) -> list[str]:
        suggestions: list[str] = []
        for item in entries:
            if item.get("status") != "ok":
                suggestions.append(f"Generate a historical backtest report for {item['symbol']} before using it in readiness gating.")
            elif not item.get("best_params"):
                suggestions.append(f"Add optimization evidence for {item['symbol']} so the workflow can keep a traceable best-parameter record.")
            elif item.get("execution_mode") == "executed":
                suggestions.append(f"Review the freshly generated best parameters for {item['symbol']} and confirm they are still appropriate for the intended {item['preferred_interval']} cadence.")
            else:
                suggestions.append(f"Reuse the stored best parameters for {item['symbol']} and verify they still match the intended {item['preferred_interval']} review cadence.")
        return list(dict.fromkeys(suggestions))

    def _build_readiness_artifact(
        self,
        *,
        task_type: str,
        health_summary: dict[str, Any],
        observations: list[Any],
        backtest_entries: list[dict[str, Any]],
        profile: dict[str, Any],
        preferred_markets: list[str],
    ) -> PlanningArtifact:
        readiness = self._build_readiness_checklist(
            task_type=task_type,
            health_summary=health_summary,
            observations=observations,
            backtest_entries=backtest_entries,
        )
        simulation_acceptance = self._collect_simulation_acceptance()
        live_evidence = self._collect_live_evidence()
        findings = [
            ResearchFinding(
                topic="workflow_readiness",
                conclusion=(
                    f"Readiness for task type {task_type} currently has {len(readiness.failed_items())} unmet item(s)."
                ),
                evidence_level="high" if not readiness.failed_items() else "mid",
                source_kind="workflow_readiness",
                confidence=0.82 if not readiness.failed_items() else 0.64,
                verification_status="verified",
            )
        ]
        next_steps = self._readiness_suggestions(readiness)
        artifact = self.hub.build_planning_artifact(
            artifact_type="beginner_quant_readiness",
            title=f"Quant Workflow Readiness ({task_type})",
            generated_at=beijing_now_isoformat(),
            assumptions=self._workflow_assumptions(profile=profile, preferred_markets=preferred_markets, task_type=task_type),
            research_findings=findings,
            research_conclusions=[finding.conclusion for finding in findings],
            execution_suggestions=next_steps,
            risk_prompts=[item.details for item in readiness.failed_items() if item.details],
            invalidation_conditions=[
                "If healthcheck evidence changes or candidate/backtest artifacts are refreshed, rerun readiness before using the result as a stage gate.",
                "If any candidate remains cadence=needs_review, keep readiness in review mode until the missing evidence is filled.",
            ],
            candidate_observations=list(observations),
            readiness=readiness,
            meta={
                "profile": dict(profile),
                "task_type": task_type,
                "preferred_markets": list(preferred_markets),
                "simulation_acceptance": simulation_acceptance,
                "live_evidence": live_evidence,
                "next_step_suggestions": next_steps,
                "readiness_failed_items": [item.name for item in readiness.failed_items()],
            },
        )
        artifact.rendered_documents = [
            self.renderer.render_markdown(artifact),
            self.renderer.render_json(artifact),
        ]
        return artifact

    def _build_readiness_checklist(
        self,
        *,
        task_type: str,
        health_summary: dict[str, Any],
        observations: list[Any],
        backtest_entries: list[dict[str, Any]],
    ) -> ReadinessChecklist:
        candidate_targets = [item for item in observations if item.selected_as != "validate_only"]
        cadence_ready = [item for item in candidate_targets if item.meta.get("trading_level") in {"daily", "minute"}]
        backtest_ok = [item for item in backtest_entries if item.get("status") == "ok"]
        optimized = [item for item in backtest_ok if item.get("best_params")]
        metrics_ready = [
            item
            for item in backtest_ok
            if item.get("performance") and item.get("sample_period", {}).get("start") and item.get("sample_period", {}).get("end")
        ]
        checks = dict(health_summary.get("checks") or {})
        items = [
            ReadinessCheckItem(
                name="healthcheck_status",
                passed=health_summary.get("status") != "blocked",
                severity="critical",
                details="Unified healthcheck must not be blocked before readiness can pass.",
                remediation="Resolve missing candidate, backtest, or task-type-specific local evidence first.",
            ),
            ReadinessCheckItem(
                name="candidate_targets_present",
                passed=bool(candidate_targets),
                severity="high",
                details="At least one observation target must survive candidate framework filtering.",
                remediation="Refresh candidate inputs and rerun candidate framework selection.",
            ),
            ReadinessCheckItem(
                name="trading_level_assignment",
                passed=bool(candidate_targets) and len(cadence_ready) == len(candidate_targets),
                severity="high",
                details="Every promoted observation target should resolve to daily or minute cadence before readiness can pass.",
                remediation="Keep cadence=needs_review symbols in observation mode until liquidity, signal, and review evidence improves.",
            ),
            ReadinessCheckItem(
                name="backtest_evidence",
                passed=bool(backtest_ok),
                severity="high",
                details="Readiness requires at least one local historical backtest evidence record for the promoted targets.",
                remediation="Generate or place the required local backtest report before stage upgrade.",
            ),
            ReadinessCheckItem(
                name="optimization_evidence",
                passed=bool(backtest_ok) and len(optimized) == len(backtest_ok),
                severity="medium" if task_type == "simulation" else "high",
                details="Optimization evidence should be present so the workflow can track best parameters for each promoted target.",
                remediation="Attach a sweep or optimization artifact for every promoted symbol.",
            ),
            ReadinessCheckItem(
                name="backtest_metrics_traceable",
                passed=bool(backtest_ok) and len(metrics_ready) == len(backtest_ok),
                severity="high",
                details="Backtest evidence should expose both sample period and performance metrics before readiness can pass.",
                remediation="Regenerate or standardize the local backtest reports so period and metrics are complete.",
            ),
        ]
        if task_type == "simulation":
            items.append(
                ReadinessCheckItem(
                    name="simulation_local_state",
                    passed=bool(checks.get("sim_session_report", {}).get("exists")) or bool(checks.get("sim_reconciliation", {}).get("exists")),
                    severity="medium",
                    details="Simulation readiness is stronger when at least one local simulation session or reconciliation artifact already exists.",
                    remediation="After the first SIM run, keep the session report and reconciliation artifact for later stage reviews.",
                )
            )
        else:
            simulation_acceptance = self._collect_simulation_acceptance()
            live_evidence = self._collect_live_evidence()
            items.extend(
                [
                    ReadinessCheckItem(
                        name="simulation_preflight_current",
                        passed=bool(simulation_acceptance.get("latest_preflight_passed")),
                        severity="critical",
                        details="Live readiness requires the latest simulation preflight report to pass.",
                        remediation="Resolve simulation preflight issues before using this workflow for live readiness.",
                    ),
                    ReadinessCheckItem(
                        name="simulation_diff_current",
                        passed=bool(simulation_acceptance.get("latest_diff_passed")),
                        severity="critical",
                        details="Live readiness requires the latest simulation diff or reconciliation report to pass.",
                        remediation="Resolve simulation diff mismatches before live-stage promotion.",
                    ),
                    ReadinessCheckItem(
                        name="live_report_schema",
                        passed=bool(live_evidence.get("report_schema_ready")),
                        severity="critical",
                        details="Live readiness requires a local live-task report schema or path.",
                        remediation="Create a local live-task report artifact before promoting to live readiness.",
                    ),
                    ReadinessCheckItem(
                        name="live_reconciliation_current",
                        passed=bool(live_evidence.get("reconciliation_recent")),
                        severity="critical",
                        details="Live readiness requires a current live reconciliation artifact.",
                        remediation="Refresh live reconciliation evidence before treating the workflow as live-ready.",
                    ),
                    ReadinessCheckItem(
                        name="live_approval_documented",
                        passed=bool(live_evidence.get("approval_switches_documented")),
                        severity="critical",
                        details="Live readiness requires explicit local approval-switch documentation.",
                        remediation="Keep the workflow below live until approval switches are explicit and auditable.",
                    ),
                ]
            )
        return ReadinessChecklist(stage=task_type, items=items)

    def _readiness_suggestions(self, readiness: ReadinessChecklist) -> list[str]:
        if not readiness.failed_items():
            return [f"Evidence for task type {readiness.stage} is sufficient for the next workflow stage review."]
        suggestions = []
        for item in readiness.failed_items():
            if item.remediation:
                suggestions.append(item.remediation)
        suggestions.append("Resolve the failed readiness items before promoting the workflow to the next stage.")
        return list(dict.fromkeys(suggestions))

    def _collect_simulation_acceptance(self) -> dict[str, Any]:
        reports_root = self.repo_root / "state" / "runs" / "reports"
        preflight_files = sorted(reports_root.glob("preflight_*.json")) if reports_root.exists() else []
        diff_files = sorted(reports_root.glob("*dual_run_diff*.json")) if reports_root.exists() else []
        latest_preflight_path = preflight_files[-1] if preflight_files else None
        latest_diff_path = diff_files[-1] if diff_files else None
        latest_preflight = self._safe_load_json(latest_preflight_path)
        latest_diff = self._safe_load_json(latest_diff_path)
        passed_preflights = [path for path in preflight_files if self._preflight_passed(self._safe_load_json(path))]
        passed_diffs = [path for path in diff_files if self._diff_report_passed(self._safe_load_json(path))]
        return {
            "required_days": 40,
            "passed_days": min(len(passed_preflights), len(passed_diffs)),
            "history_points": max(len(preflight_files), len(diff_files)),
            "latest_preflight_passed": self._preflight_passed(latest_preflight),
            "latest_diff_passed": self._diff_report_passed(latest_diff),
            "latest_preflight_path": str(latest_preflight_path) if latest_preflight_path else "",
            "latest_report_path": str(latest_diff_path) if latest_diff_path else "",
        }

    def _collect_live_evidence(self) -> dict[str, Any]:
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
        }

    def _workflow_assumptions(self, *, profile: dict[str, Any], preferred_markets: list[str], task_type: str) -> list[PlanAssumption]:
        return [
            PlanAssumption(
                name="task_type",
                value=task_type,
                reason="Healthcheck and readiness evidence requirements differ for simulation and live-oriented reviews.",
            ),
            PlanAssumption(
                name="preferred_markets",
                value=",".join(preferred_markets) or "all_local_markets",
                reason="Candidate framework filtering stays aligned with the requested market subset.",
            ),
            PlanAssumption(
                name="workflow_mode",
                value=str(profile.get("risk_profile") or "conservative"),
                reason="Risk profile is retained as user context even though the workflow now focuses on evidence gathering instead of personal planning.",
            ),
        ]

    def _finalize_workflow(
        self,
        *,
        workflow_name: str,
        mode: str,
        task_type: str,
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
            "task_type": task_type,
            "started_at": started_at,
            "status": "blocked" if any(step.status == "blocked" for step in steps) else ("warning" if any(step.status == "warning" for step in steps) else "ok"),
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
            "warning_steps": [step["step"] for step in workflow["steps"] if step["status"] == "warning"],
            "task_type": task_type,
        }
        workflow.update(artifact_paths)
        return workflow

    def _load_health_snapshot(self, *, mode: str) -> dict[str, Any]:
        path = self.repo_root / "state" / "runs" / "healthcheck.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.setdefault("source", "cached_healthcheck")
            payload.setdefault("path", str(path))
            payload.setdefault("alerts", payload.get("alerts", []))
            return payload
        if self.allow_remote_checks and mode not in {"plan", "healthcheck_only"}:
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

    def _safe_load_json(self, path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _latest_matching_path(self, root: Path, pattern: str) -> Path | None:
        if not root.exists():
            return None
        matches = sorted(root.glob(pattern))
        return matches[-1] if matches else None

    def _preflight_passed(self, payload: dict[str, Any]) -> bool:
        totals = payload.get("totals") or {}
        return bool(payload) and int(totals.get("fail") or 0) == 0

    def _diff_report_passed(self, payload: dict[str, Any]) -> bool:
        totals = payload.get("totals") or {}
        business_keys = payload.get("business_keys") or {}
        return bool(payload) and int(totals.get("fail") or 0) == 0 and int(business_keys.get("only_a_total_count") or 0) == 0 and int(business_keys.get("only_b_total_count") or 0) == 0

