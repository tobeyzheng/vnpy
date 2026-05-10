from __future__ import annotations

from typing import Any

from .models import ReadinessCheckItem, ReadinessChecklist


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
                    "Manual bias review is still required before stage upgrade.",
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
                remediation="Add start/end fields before using the result for stage upgrade.",
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
                details="Train/validation/test split must be recorded before stage upgrade.",
                remediation="Add explicit train, validation, and test ranges to the backtest metadata.",
            ),
            ReadinessCheckItem(
                name="out_of_sample_or_stability",
                passed=bool(metadata.get("out_of_sample") or stability_metrics.get("sharpe_ratio") is not None or stability_metrics.get("return_drawdown_ratio") is not None),
                severity="medium",
                details="A stage-upgrade candidate should include out-of-sample evidence or stability metrics.",
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
                details="Liquidity assumptions must be documented for beginner-safe stage upgrades.",
                remediation="Record spread, fill, or liquidity assumptions in the backtest report.",
            ),
            ReadinessCheckItem(
                name="data_quality_notes",
                passed=bool(data_quality or metadata.get("quality_notes")),
                severity="high",
                details="Data quality and bias notes must be recorded.",
                remediation="Document look-ahead, survivor bias, missing values and corporate-action handling.",
            ),
            ReadinessCheckItem(
                name="bias_review",
                passed=not any(flag in {"look_ahead_bias", "survivor_bias", "corporate_action_incomplete"} for flag in bias_flags),
                severity="high",
                details="Known look-ahead, survivor bias, or incomplete corporate-action handling should block stage upgrade.",
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
                    remediation="Resolve or explicitly scope the incomplete data-quality issue before upgrade.",
                )
            )
        return ReadinessChecklist(stage="backtest", items=items)

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
    ) -> ReadinessChecklist:
        gaps = list(capability_gaps or [])
        items = [
            ReadinessCheckItem(
                name="health_status",
                passed=health_status != "blocked",
                severity="critical",
                details="Environment health must not be blocked.",
                remediation="Resolve healthcheck alerts before stage upgrade.",
            ),
            ReadinessCheckItem(
                name="research_artifact",
                passed=has_research_artifact,
                severity="high",
                details="A structured research artifact should exist before upgrade.",
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
                details="Recent review notes should exist before upgrading stages.",
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
                    details="A weekly observation and recap process should already exist before simulation upgrade.",
                    remediation="Accumulate review notes over the minimum observation window before upgrading.",
                )
            )
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
            items.append(
                ReadinessCheckItem(
                    name="manual_approval_path",
                    passed=False,
                    severity="critical",
                    details="Live-stage planning still requires explicit manual approval, reconciliation, and local protection checks.",
                    remediation="Keep the workflow in simulation/report mode until the manual approval path is confirmed.",
                )
            )
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
            }
        if stage == "live":
            return {
                "minimum_observation_days": 20,
                "minimum_simulation_days": 40,
                "pass_standard": "Explicit approval controls, current reconciliation, validated simulation history, and no critical readiness failures.",
            }
        return {
            "minimum_observation_days": 5,
            "minimum_simulation_days": 0,
            "pass_standard": "Basic research and review structure established.",
        }
