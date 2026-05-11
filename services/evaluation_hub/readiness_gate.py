from __future__ import annotations

from typing import Any

from .models import CandidateObservation, ReadinessCheckItem, ReadinessChecklist


class ReadinessGateService:
    def normalize_backtest_report(self, report: dict[str, Any]) -> dict[str, Any]:
        setting = report.get("setting") or {}
        stats = report.get("stats") or {}
        validation = report.get("validation") or report.get("validation_split") or {}
        data_quality = report.get("data_quality") or {}
        sample_count = (
            stats.get("sample_count")
            or stats.get("bar_count")
            or stats.get("trade_count")
            or stats.get("total_trade_count")
        )
        turnover = stats.get("turnover")
        liquidity_assumptions = report.get("liquidity_assumptions") or setting.get("liquidity_assumptions") or []
        return {
            "start": stats.get("start_date") or report.get("start") or report.get("start_date"),
            "end": stats.get("end_date") or report.get("end") or report.get("end_date"),
            "rate": report.get("rate") if report.get("rate") is not None else setting.get("rate", 0.0),
            "slippage": report.get("slippage") if report.get("slippage") is not None else setting.get("slippage", 0.0),
            "turnover": turnover,
            "sample_count": sample_count,
            "liquidity_assumptions": liquidity_assumptions,
            "out_of_sample": report.get("out_of_sample") or validation.get("out_of_sample"),
            "validation_split": {
                "train": validation.get("train") or validation.get("train_range") or report.get("train_range"),
                "validation": validation.get("validation") or validation.get("validation_range") or report.get("validation_range"),
                "test": validation.get("test") or validation.get("test_range") or report.get("test_range"),
            },
            "stability_metrics": {
                "sharpe_ratio": stats.get("sharpe_ratio"),
                "return_drawdown_ratio": stats.get("return_drawdown_ratio"),
                "trade_count": stats.get("total_trade_count") or stats.get("trade_count_runtime"),
                "max_drawdown": stats.get("max_drawdown"),
                "turnover": turnover,
            },
            "data_quality": data_quality or {
                "notes": [
                    "Corporate action handling is not explicitly recorded in the raw vnpy report.",
                    "Manual bias review is still required before promotion.",
                ],
                "bias_flags": ["look_ahead_review_required", "survivor_bias_review_required"],
            },
        }

    def validate_backtest_metadata(self, metadata: dict[str, Any]) -> ReadinessChecklist:
        validation_split = metadata.get("validation_split") or {}
        stability_metrics = metadata.get("stability_metrics") or {}
        data_quality = metadata.get("data_quality") or {}
        bias_flags = [str(item) for item in data_quality.get("bias_flags", [])]
        quality_notes = list(data_quality.get("notes", [])) if isinstance(data_quality, dict) else []
        turnover = metadata.get("turnover") if metadata.get("turnover") is not None else stability_metrics.get("turnover")
        sample_count = metadata.get("sample_count")
        liquidity_assumptions = metadata.get("liquidity_assumptions") or []
        items = [
            ReadinessCheckItem(
                name="sample_period",
                passed=bool(metadata.get("start") and metadata.get("end")),
                severity="high",
                details="Backtest must record a clear sample period.",
                remediation="Add start/end fields before using the result for readiness or promotion.",
            ),
            ReadinessCheckItem(
                name="transaction_costs",
                passed=metadata.get("rate") is not None and metadata.get("slippage") is not None,
                severity="high",
                details="Fees and slippage assumptions must both be present.",
                remediation="Record both fee and slippage assumptions explicitly.",
            ),
            ReadinessCheckItem(
                name="validation_split",
                passed=all(validation_split.get(key) for key in ("train", "validation", "test")),
                severity="high",
                details="Train/validation/test split must be recorded before promotion.",
                remediation="Add explicit train, validation, and test ranges to the backtest metadata.",
            ),
            ReadinessCheckItem(
                name="out_of_sample_or_stability",
                passed=bool(metadata.get("out_of_sample") or stability_metrics.get("sharpe_ratio") is not None or stability_metrics.get("return_drawdown_ratio") is not None),
                severity="medium",
                details="A promotion candidate should include out-of-sample evidence or stability metrics.",
                remediation="Add out_of_sample notes and at least one stability metric.",
            ),
            ReadinessCheckItem(
                name="turnover_metric",
                passed=turnover is not None,
                severity="medium",
                details="Turnover should be recorded so execution sensitivity can be reviewed.",
                remediation="Attach turnover or an equivalent trading-activity metric.",
            ),
            ReadinessCheckItem(
                name="sample_count",
                passed=sample_count is not None and float(sample_count) > 0,
                severity="medium",
                details="Sample count should be recorded before treating the result as robust.",
                remediation="Add the number of bars, observations, or trades used by the backtest.",
            ),
            ReadinessCheckItem(
                name="liquidity_assumptions",
                passed=bool(liquidity_assumptions),
                severity="high",
                details="Liquidity assumptions must be documented for stage upgrades.",
                remediation="Record spread, fill, or liquidity assumptions in the backtest report.",
            ),
            ReadinessCheckItem(
                name="data_quality_notes",
                passed=bool(data_quality or metadata.get("quality_notes")),
                severity="high",
                details="Data quality and bias notes must be recorded.",
                remediation="Document look-ahead, survivor bias, missing values, and corporate-action handling.",
            ),
            ReadinessCheckItem(
                name="bias_review",
                passed=not any(flag in {"look_ahead_bias", "survivor_bias", "corporate_action_incomplete"} for flag in bias_flags),
                severity="high",
                details="Known look-ahead, survivor bias, or incomplete corporate-action handling should block promotion.",
                remediation="Clear or downgrade the unstable result until the bias issue is addressed.",
            ),
        ]
        if quality_notes and any("missing" in note.lower() or "incomplete" in note.lower() for note in quality_notes):
            items.append(
                ReadinessCheckItem(
                    name="quality_note_warnings",
                    passed=False,
                    severity="medium",
                    details="Quality notes mention missing or incomplete handling that still needs manual review.",
                    remediation="Resolve or explicitly scope the incomplete data-quality issue before promotion.",
                )
            )
        return ReadinessChecklist(stage="backtest", items=items)

    def validate_candidate_readiness(
        self,
        observations: list[dict[str, Any]] | list[CandidateObservation],
        *,
        stage: str,
    ) -> ReadinessChecklist:
        normalized: list[dict[str, Any]] = []
        for item in observations:
            if isinstance(item, CandidateObservation):
                normalized.append(
                    {
                        "symbol": item.symbol,
                        "bucket": item.bucket or item.selected_as,
                        "trading_level": item.trading_level or item.meta.get("trading_level"),
                        "backtest_ready": bool(item.backtest_ready or item.meta.get("backtest_ready")),
                        "manual_review_required": bool(item.manual_review_required or item.meta.get("manual_review_required")),
                        "hard_risk_flags": list(item.hard_risk_flags or item.meta.get("hard_risk_flags") or []),
                    }
                )
            else:
                payload = dict(item)
                meta = dict(payload.get("meta") or {})
                normalized.append(
                    {
                        "symbol": str(payload.get("symbol") or ""),
                        "bucket": str(payload.get("bucket") or meta.get("bucket") or payload.get("selected_as") or ""),
                        "trading_level": str(payload.get("trading_level") or meta.get("trading_level") or ""),
                        "backtest_ready": bool(payload.get("backtest_ready") if payload.get("backtest_ready") is not None else meta.get("backtest_ready")),
                        "manual_review_required": bool(payload.get("manual_review_required") if payload.get("manual_review_required") is not None else meta.get("manual_review_required")),
                        "hard_risk_flags": list(payload.get("hard_risk_flags") or meta.get("hard_risk_flags") or []),
                    }
                )

        active_buckets = {"priority_trade", "active_watch", "research_queue"}
        active = [item for item in normalized if item["bucket"] in active_buckets and not item["hard_risk_flags"]]
        priority = [item for item in active if item["bucket"] == "priority_trade"]
        cadence_ready = [item for item in active if item["trading_level"] in {"daily", "minute"}]
        backtest_ready = [item for item in active if item["backtest_ready"]]
        unresolved_hard_risk = [item for item in normalized if item["bucket"] in active_buckets and item["hard_risk_flags"]]
        items = [
            ReadinessCheckItem(
                name="active_candidates_present",
                passed=bool(active),
                severity="high",
                details="At least one active candidate should remain after applying bucket and hard-risk filters.",
                remediation="Refresh candidate inputs or relax only the quantitative bucket thresholds, not the hard-risk blocks.",
            ),
            ReadinessCheckItem(
                name="candidate_trading_level",
                passed=bool(active) and len(cadence_ready) == len(active),
                severity="high",
                details="Every active candidate should resolve to daily or minute cadence before promotion.",
                remediation="Keep cadence=needs_review symbols in research_queue until liquidity, signal, and review evidence improves.",
            ),
            ReadinessCheckItem(
                name="candidate_backtest_ready",
                passed=bool(backtest_ready),
                severity="high" if stage in {"backtest", "simulation", "live"} else "medium",
                details="At least one active candidate should be explicitly marked as backtest-ready.",
                remediation="Promote only candidates with explicit cadence and sufficient structured evidence into the backtest set.",
            ),
            ReadinessCheckItem(
                name="candidate_hard_risk_clear",
                passed=not unresolved_hard_risk,
                severity="high" if stage in {"simulation", "live"} else "medium",
                details="Active candidates should not carry unresolved hard risk flags.",
                remediation="Move hard-risk candidates to exclude or research_queue until unsupported execution, thin liquidity, or stale data is resolved.",
            ),
        ]
        if stage in {"simulation", "live"}:
            items.append(
                ReadinessCheckItem(
                    name="priority_trade_present",
                    passed=bool(priority),
                    severity="medium" if stage == "simulation" else "high",
                    details="Simulation and live-adjacent reviews are stronger when at least one priority-trade candidate exists.",
                    remediation="Keep simulation or live promotion tied to candidates with stronger score, liquidity, and risk-penalty support.",
                )
            )
        if stage == "live":
            items.append(
                ReadinessCheckItem(
                    name="manual_review_cleared",
                    passed=not any(item["manual_review_required"] for item in priority),
                    severity="medium",
                    details="Priority-trade candidates should have manual review notes closed before live promotion.",
                    remediation="Resolve remaining thesis-review notes before treating the workflow as live-ready.",
                )
            )
        return ReadinessChecklist(stage=f"candidate_{stage}", items=items)

    def build_stage_checklist(
        self,
        *,
        stage: str,
        health_status: str = "ok",
        has_research_artifact: bool = False,
        has_backtest_metadata: bool = False,
        has_risk_budget: bool = False,
        has_review_notes: bool = False,
        capability_gaps: list[str] | None = None,
        simulation_acceptance: dict[str, Any] | None = None,
        live_evidence: dict[str, Any] | None = None,
    ) -> ReadinessChecklist:
        gaps = list(capability_gaps or [])
        sim_evidence = self._normalize_simulation_acceptance(simulation_acceptance, stage=stage)
        live_checks = self._normalize_live_evidence(live_evidence)
        items = [
            ReadinessCheckItem(
                name="health_status",
                passed=health_status != "blocked",
                severity="critical",
                details="Environment health must not be blocked.",
                remediation="Resolve healthcheck alerts before promotion.",
            ),
            ReadinessCheckItem(
                name="research_artifact",
                passed=has_research_artifact,
                severity="high",
                details="A structured research artifact should exist before promotion.",
                remediation="Generate and review the research artifact first.",
            ),
            ReadinessCheckItem(
                name="risk_budget",
                passed=has_risk_budget,
                severity="high",
                details="A written risk budget should exist before simulation or higher stages.",
                remediation="Generate a plan with explicit risk budget and stop conditions.",
            ),
            ReadinessCheckItem(
                name="review_notes",
                passed=has_review_notes,
                severity="medium",
                details="Recent review notes should exist before promoting stages.",
                remediation="Add weekly review and recap notes.",
            ),
        ]
        if stage in {"simulation", "live"}:
            items.append(
                ReadinessCheckItem(
                    name="backtest_validation",
                    passed=has_backtest_metadata,
                    severity="high",
                    details="Simulation or live-adjacent planning requires validated backtest metadata.",
                    remediation="Add sample period, costs, split ranges, turnover, and data quality notes.",
                )
            )
            items.append(
                ReadinessCheckItem(
                    name="minimum_observation_process",
                    passed=has_review_notes,
                    severity="medium",
                    details="A weekly observation and recap process should already exist before simulation promotion.",
                    remediation="Accumulate review notes over the minimum observation window before promoting.",
                )
            )
            items.extend(self._simulation_acceptance_items(sim_evidence, stage=stage))
        if stage == "live":
            items.append(
                ReadinessCheckItem(
                    name="capability_gaps",
                    passed=not gaps,
                    severity="critical",
                    details="Local capability gaps must be resolved before live-stage planning.",
                    remediation="Close the missing local capability gaps or keep the workflow below live stage.",
                )
            )
            items.extend(self._live_evidence_items(live_checks))
        return ReadinessChecklist(stage=stage, items=items)

    def should_block_upgrade(self, checklist: ReadinessChecklist) -> tuple[bool, list[str]]:
        failures = checklist.failed_items()
        reasons = [item.details for item in failures if item.details]
        block = any(item.severity in {"high", "critical"} for item in failures)
        return block, reasons

    def minimum_observation_requirements(self, stage: str) -> dict[str, Any]:
        if stage == "simulation":
            return {
                "minimum_observation_days": 10,
                "minimum_simulation_days": 20,
                "pass_standard": "No unresolved health/data blockers, validated backtest metadata, and a stable weekly review process.",
                "required_artifacts": ["state/runs/reports/preflight_*.json", "state/runs/reports/*dual_run_diff*.json"],
            }
        if stage == "live":
            return {
                "minimum_observation_days": 20,
                "minimum_simulation_days": 40,
                "pass_standard": "Explicit approval controls, current reconciliation, validated simulation history, and no critical readiness failures.",
                "required_artifacts": ["state/runs/reports/preflight_*.json", "state/runs/reports/*dual_run_diff*.json", "state/runs/hk_live_task_report.json|state/runs/us_live_task_report.json"],
            }
        return {
            "minimum_observation_days": 5,
            "minimum_simulation_days": 0,
            "pass_standard": "Basic research and review structure established.",
            "required_artifacts": [],
        }

    def _normalize_simulation_acceptance(self, evidence: dict[str, Any] | None, *, stage: str) -> dict[str, Any]:
        payload = dict(evidence or {})
        minimums = self.minimum_observation_requirements(stage)
        required_days = int(payload.get("required_days") or minimums.get("minimum_simulation_days") or 0)
        passed_days = int(payload.get("passed_days") or 0)
        history_points = int(payload.get("history_points") or 0)
        latest_preflight_passed = bool(payload.get("latest_preflight_passed"))
        latest_diff_passed = bool(payload.get("latest_diff_passed"))
        return {
            "required_days": required_days,
            "passed_days": passed_days,
            "history_points": history_points,
            "latest_preflight_passed": latest_preflight_passed,
            "latest_diff_passed": latest_diff_passed,
            "latest_report_path": str(payload.get("latest_report_path") or ""),
            "latest_preflight_path": str(payload.get("latest_preflight_path") or ""),
            "history_summary": list(payload.get("history_summary") or []),
        }

    def _simulation_acceptance_items(self, evidence: dict[str, Any], *, stage: str) -> list[ReadinessCheckItem]:
        required_days = int(evidence.get("required_days") or 0)
        passed_days = int(evidence.get("passed_days") or 0)
        latest_report = str(evidence.get("latest_report_path") or "")
        latest_preflight = str(evidence.get("latest_preflight_path") or "")
        items = [
            ReadinessCheckItem(
                name="simulation_acceptance_window",
                passed=required_days <= 0 or passed_days >= required_days,
                severity="high" if stage == "live" else "medium",
                details=(
                    f"Simulation acceptance should include at least {required_days} passing day(s); "
                    f"current passing history is {passed_days} day(s)."
                ),
                remediation="Accumulate additional passing SIM/preflight evidence before promotion.",
            ),
            ReadinessCheckItem(
                name="simulation_preflight_current",
                passed=bool(evidence.get("latest_preflight_passed")),
                severity="high",
                details="The latest SIM preflight report should pass before promotion.",
                remediation="Resolve worktree/config/residual-order issues until the latest preflight is green.",
            ),
            ReadinessCheckItem(
                name="simulation_diff_current",
                passed=bool(evidence.get("latest_diff_passed")),
                severity="high",
                details="The latest SIM reconciliation/diff report should pass before promotion.",
                remediation="Resolve dual-run or reconciliation differences before promotion.",
            ),
        ]
        if latest_report or latest_preflight:
            items.append(
                ReadinessCheckItem(
                    name="simulation_artifact_traceability",
                    passed=True,
                    severity="low",
                    details=f"Latest SIM evidence: diff={latest_report or 'n/a'} preflight={latest_preflight or 'n/a'}.",
                    remediation="",
                )
            )
        return items

    def _normalize_live_evidence(self, evidence: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(evidence or {})
        return {
            "report_schema_ready": bool(payload.get("report_schema_ready")),
            "approval_switches_documented": bool(payload.get("approval_switches_documented")),
            "reconciliation_recent": bool(payload.get("reconciliation_recent")),
            "risk_guard_auditable": bool(payload.get("risk_guard_auditable")),
            "report_path": str(payload.get("report_path") or ""),
            "reconciliation_path": str(payload.get("reconciliation_path") or ""),
            "approval_notes": list(payload.get("approval_notes") or []),
        }

    def _live_evidence_items(self, evidence: dict[str, Any]) -> list[ReadinessCheckItem]:
        manual_path_ready = all(
            [
                evidence.get("report_schema_ready"),
                evidence.get("approval_switches_documented"),
                evidence.get("reconciliation_recent"),
                evidence.get("risk_guard_auditable"),
            ]
        )
        return [
            ReadinessCheckItem(
                name="live_report_schema",
                passed=bool(evidence.get("report_schema_ready")),
                severity="high",
                details="A live report path/schema should be in place so gating and order audit evidence can be persisted.",
                remediation="Add or validate the live-task report path and output schema before promotion.",
            ),
            ReadinessCheckItem(
                name="approval_switches_documented",
                passed=bool(evidence.get("approval_switches_documented")),
                severity="critical",
                details="Live approval switches and explicit submit intent must be documented in the local workflow.",
                remediation="Keep live in dry-run/report mode until approval switches are explicit and auditable.",
            ),
            ReadinessCheckItem(
                name="reconciliation_current",
                passed=bool(evidence.get("reconciliation_recent")),
                severity="critical",
                details="Live-stage planning requires a current reconciliation artifact.",
                remediation="Refresh reconciliation evidence before promotion.",
            ),
            ReadinessCheckItem(
                name="risk_audit_trail",
                passed=bool(evidence.get("risk_guard_auditable")),
                severity="high",
                details="Execution-guard and risk decisions should leave an auditable local trail.",
                remediation="Ensure live report/event artifacts expose gating and risk decisions.",
            ),
            ReadinessCheckItem(
                name="manual_approval_path",
                passed=manual_path_ready,
                severity="critical",
                details="Live-stage planning requires explicit manual approval, reconciliation, and local protection evidence.",
                remediation="Keep the workflow in simulation/report mode until the manual approval path is evidenced locally.",
            ),
        ]
