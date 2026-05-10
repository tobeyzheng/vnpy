from __future__ import annotations

from typing import Any

from .models import ReadinessCheckItem, ReadinessChecklist


class ReadinessGateService:
    def normalize_backtest_report(self, report: dict[str, Any]) -> dict[str, Any]:
        setting = report.get("setting") or {}
        stats = report.get("stats") or {}
        return {
            "start": stats.get("start_date") or report.get("start") or report.get("start_date"),
            "end": stats.get("end_date") or report.get("end") or report.get("end_date"),
            "rate": report.get("rate") if report.get("rate") is not None else setting.get("rate", 0.0),
            "slippage": report.get("slippage") if report.get("slippage") is not None else setting.get("slippage", 0.0),
            "stability_metrics": {
                "sharpe_ratio": stats.get("sharpe_ratio"),
                "return_drawdown_ratio": stats.get("return_drawdown_ratio"),
                "trade_count": stats.get("total_trade_count") or stats.get("trade_count_runtime"),
            },
            "data_quality": report.get("data_quality") or {
                "notes": [
                    "Corporate action handling is not explicitly recorded in the raw vnpy report.",
                    "Manual bias review is still required before stage upgrade.",
                ]
            },
        }

    def validate_backtest_metadata(self, metadata: dict[str, Any]) -> ReadinessChecklist:
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
                name="stability_metrics",
                passed=bool(metadata.get("stability_metrics") or metadata.get("out_of_sample") or metadata.get("validation_split")),
                severity="medium",
                details="A stage-upgrade candidate should include at least one stability or out-of-sample note.",
                remediation="Add out_of_sample or stability_metrics notes.",
            ),
            ReadinessCheckItem(
                name="data_quality_notes",
                passed=bool(metadata.get("data_quality") or metadata.get("quality_notes")),
                severity="high",
                details="Data quality and bias notes must be recorded.",
                remediation="Document look-ahead, survivor bias, missing values and corporate-action handling.",
            ),
        ]
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
                    remediation="Add sample period, costs, stability and data quality notes.",
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
                "pass_standard": "No unresolved health/data blockers and a stable weekly review process.",
            }
        if stage == "live":
            return {
                "minimum_observation_days": 20,
                "minimum_simulation_days": 40,
                "pass_standard": "Explicit approval controls, current reconciliation, and no critical readiness failures.",
            }
        return {
            "minimum_observation_days": 5,
            "minimum_simulation_days": 0,
            "pass_standard": "Basic research and review structure established.",
        }
