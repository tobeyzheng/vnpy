from __future__ import annotations

import argparse
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.evaluation_hub import CapabilityRegistry
from services.evaluation_hub.beginner_candidate_selector import BeginnerCandidateSelector
from services.evaluation_hub.candidate_framework import BeginnerCandidateFramework
from services.evaluation_hub.doc_renderer import BeginnerExplanationRenderer
from services.evaluation_hub.evidence_standardizer import EvidenceStandardizer
from services.evaluation_hub.plan_generator import BeginnerPlanGenerator
from services.evaluation_hub.readiness_gate import ReadinessGateService
from services.strategy.candidate_preparation import CandidateInputPreparationService
from scripts.quant_workflow import __main__ as quant_workflow_module
from scripts.quant_workflow.run_prepare_candidate_inputs import build_parser as build_prepare_candidate_parser
from scripts.quant_workflow.run_quant_workflow import PRESET_WORKFLOWS, _cli_summary, _resolve_workflow_args, build_parser as build_quant_workflow_parser
from scripts.quant_workflow.workflow_service import QuantWorkflowService


class BeginnerQuantWorkflowTests(unittest.TestCase):
    def test_capability_registry_exposes_hk_capabilities_without_gaps(self):
        registry = CapabilityRegistry()

        gaps = registry.capability_gaps()

        self.assertFalse(any(gap.expected_path.startswith("scripts/run_hk") for gap in gaps))
        self.assertTrue(any(item.capability_id == "sim.hk_task" for item in registry.select_for_stage("simulation")))
        self.assertTrue(any(item.capability_id == "live.hk_task" for item in registry.select_for_stage("live")))
        self.assertTrue(any(item.capability_id == "classic_multifactor.vnpy_backtest" for item in registry.select_for_stage("backtest")))

    def test_candidate_framework_downgrades_high_risk_or_unsupported_market(self):
        framework = BeginnerCandidateFramework()

        observations = framework.build_observation_list(
            [
                {
                    "symbol": "NVDA.US",
                    "market": "us",
                    "name": "NVIDIA",
                    "rationale": "AI leader with persistent demand",
                    "risk": "execution and valuation risk",
                    "raw_score": 0.86,
                    "signals": [{"score": 0.84, "summary": "trend intact"}],
                },
                {
                    "symbol": "300308.SZ",
                    "market": "a_share",
                    "name": "中际旭创",
                    "rationale": "AI theme beta",
                    "risk": "高波动",
                    "raw_score": 0.90,
                },
            ],
            preferred_markets=["us"],
            max_items=5,
        )

        self.assertIn(observations[0].selected_as, {"beginner_watchlist", "observe_only"})
        self.assertTrue(any(item.market == "a_share" and item.selected_as == "validate_only" for item in observations))

    def test_readiness_gate_blocks_simulation_without_backtest_metadata(self):
        gate = ReadinessGateService()

        checklist = gate.build_stage_checklist(
            stage="simulation",
            health_status="ok",
            has_research_artifact=True,
            has_backtest_metadata=False,
            has_risk_budget=True,
            has_review_notes=True,
        )
        blocked, reasons = gate.should_block_upgrade(checklist)

        self.assertTrue(blocked)
        self.assertTrue(any("validated backtest metadata" in reason for reason in reasons))

    def test_readiness_gate_requires_split_turnover_liquidity_and_bias_cleanliness(self):
        gate = ReadinessGateService()

        checklist = gate.validate_backtest_metadata(
            {
                "start": "2024-01-01",
                "end": "2024-12-31",
                "rate": 0.0005,
                "slippage": 0.0008,
                "validation_split": {"train": "2024-01-01:2024-06-30"},
                "data_quality": {"notes": ["missing corporate action review"], "bias_flags": ["look_ahead_bias"]},
            }
        )
        blocked, reasons = gate.should_block_upgrade(checklist)
        failed_names = {item.name for item in checklist.failed_items()}

        self.assertTrue(blocked)
        self.assertIn("validation_split", failed_names)
        self.assertIn("turnover_metric", failed_names)
        self.assertIn("liquidity_assumptions", failed_names)
        self.assertIn("bias_review", failed_names)
        self.assertTrue(any("Train/validation/test split" in reason for reason in reasons))

    def test_readiness_gate_accepts_complete_backtest_metadata(self):
        gate = ReadinessGateService()

        checklist = gate.validate_backtest_metadata(
            {
                "start": "2024-01-01",
                "end": "2024-12-31",
                "rate": 0.0005,
                "slippage": 0.0008,
                "turnover": 1.6,
                "sample_count": 240,
                "liquidity_assumptions": ["Large-cap daily bars with manual spread review"],
                "out_of_sample": "2024-10-01:2024-12-31",
                "validation_split": {
                    "train": "2024-01-01:2024-06-30",
                    "validation": "2024-07-01:2024-09-30",
                    "test": "2024-10-01:2024-12-31",
                },
                "stability_metrics": {"sharpe_ratio": 1.1, "return_drawdown_ratio": 1.4, "turnover": 1.6},
                "data_quality": {"notes": ["manual bias review completed"], "bias_flags": []},
            }
        )
        blocked, _ = gate.should_block_upgrade(checklist)

        self.assertFalse(blocked)
        self.assertFalse(checklist.failed_items())

    def test_readiness_gate_uses_simulation_and_live_evidence(self):
        gate = ReadinessGateService()

        checklist = gate.build_stage_checklist(
            stage="live",
            health_status="ok",
            has_research_artifact=True,
            has_backtest_metadata=True,
            has_risk_budget=True,
            has_review_notes=True,
            capability_gaps=[],
            simulation_acceptance={
                "required_days": 40,
                "passed_days": 40,
                "latest_preflight_passed": True,
                "latest_diff_passed": True,
                "latest_preflight_path": "state/runs/reports/preflight_20260510.json",
                "latest_report_path": "state/runs/reports/dual_run_diff_20260510.json",
            },
            live_evidence={
                "report_schema_ready": True,
                "approval_switches_documented": True,
                "reconciliation_recent": True,
                "risk_guard_auditable": True,
                "report_path": "state/runs/hk_live_task_report.json",
                "reconciliation_path": "state/runs/futu_live_position_reconcile.json",
            },
        )

        blocked, reasons = gate.should_block_upgrade(checklist)

        self.assertFalse(blocked)
        self.assertFalse(reasons)
        self.assertFalse(checklist.failed_items())

    def test_plan_generator_records_profile_changes_against_previous_plan(self):
        generator = BeginnerPlanGenerator()
        previous = generator.build_plan(profile={"preferred_market": "us", "risk_profile": "conservative"}, observations=[])

        current = generator.build_plan(
            profile={"preferred_market": "hong_kong", "risk_profile": "moderate"},
            observations=[],
            previous_plan=previous,
        )

        diffs = current.meta.get("plan_differences") or []
        self.assertTrue(any(item["field"] == "preferred_market" for item in diffs))
        self.assertTrue(any(item["field"] == "risk_profile" for item in diffs))

    def test_plan_generator_uses_conservative_defaults_when_profile_is_missing(self):
        generator = BeginnerPlanGenerator()

        plan = generator.build_plan(profile={}, observations=[])

        self.assertEqual(plan.meta["profile"]["capital"], "unknown_keep_small")
        self.assertEqual(plan.meta["profile"]["hours_per_week"], 5.0)
        self.assertEqual(plan.meta["profile"]["max_drawdown_pct"], 8.0)
        self.assertEqual(plan.risk_budget.max_positions, 3)
        self.assertIn("full_plan_initialization", plan.meta["profile_update_scope"])

    def test_plan_generator_personalization_changes_budget_and_update_scope(self):
        generator = BeginnerPlanGenerator()
        previous = generator.build_plan(
            profile={
                "preferred_market": "us",
                "risk_profile": "conservative",
                "capital": 30000,
                "hours_per_week": 6,
                "max_drawdown_pct": 8,
                "preferred_cadence": "low_frequency",
            },
            observations=[],
        )

        current = generator.build_plan(
            profile={
                "preferred_market": "us",
                "risk_profile": "moderate",
                "capital": 5000,
                "hours_per_week": 3,
                "max_drawdown_pct": 5,
                "preferred_cadence": "intraday",
            },
            observations=[],
            previous_plan=previous,
        )

        self.assertEqual(current.meta["personalization_summary"]["capital_bucket"], "micro")
        self.assertEqual(current.meta["personalization_summary"]["time_budget_bucket"], "limited")
        self.assertIn("risk_budget", current.meta["profile_update_scope"])
        self.assertIn("phase_schedule", current.meta["profile_update_scope"])
        self.assertLessEqual(current.risk_budget.max_positions, 2)
        self.assertLessEqual(current.risk_budget.total_exposure_limit_pct, 0.18)
        self.assertTrue(any("turnover or execution sensitivity" in item for item in current.risk_budget.stop_conditions))

    def test_renderer_includes_plan_differences_and_next_steps(self):
        generator = BeginnerPlanGenerator()
        renderer = BeginnerExplanationRenderer()
        previous = generator.build_plan(profile={"preferred_market": "us", "risk_profile": "conservative"}, observations=[])
        current = generator.build_plan(
            profile={"preferred_market": "hong_kong", "risk_profile": "moderate"},
            observations=[],
            previous_plan=previous,
        )
        current.meta["previous_plan_loaded"] = True
        current.meta["next_step_suggestions"] = ["Resolve readiness failures before upgrading to the next stage."]

        markdown = renderer.render_markdown(current).body
        payload = json.loads(renderer.render_json(current).body)

        self.assertIn("### What changed from the previous plan", markdown)
        self.assertIn("preferred_market", markdown)
        self.assertIn("### Personalization summary", markdown)
        self.assertIn("### Update scope", markdown)
        self.assertIn("### Next actions", markdown)
        self.assertTrue(any("preferred_market" in item for item in payload["plan_differences"]))
        self.assertIn("Resolve readiness failures before upgrading to the next stage.", payload["next_actions"])
        self.assertIn("personalization_summary", payload)
        self.assertIn("update_scope", payload)

    def test_evidence_standardizer_marks_conflicts_and_pending_verification(self):
        standardizer = EvidenceStandardizer()

        result = standardizer.standardize_llm_research_result(
            {
                "references": [
                    {
                        "title": "AQR trend research",
                        "url": "https://www.aqr.com/Research/example",
                        "published_at": "2024-01-01",
                        "evidence_level": "high",
                        "summary": "Well-known industry reference",
                    },
                    {
                        "title": "SEC market structure bulletin",
                        "url": "https://www.sec.gov/example",
                        "published_at": "2024-02-01",
                        "evidence_level": "high",
                        "summary": "Regulatory guidance on execution assumptions",
                    },
                    {
                        "title": "University portfolio construction note",
                        "url": "https://example.edu/quant-note",
                        "published_at": "2024-03-01",
                        "evidence_level": "high",
                        "summary": "Academic note on diversification trade-offs",
                    }
                ],
                "core_concepts": [
                    "Quant research should separate evidence from opinion.",
                    "Validation rules should be written before stage upgrades.",
                ],
                "beginner_safe_practices": [
                    "Start with low-frequency and reviewable workflows.",
                    "Keep the active universe small until review notes are stable.",
                ],
                "conflicting_viewpoints": [
                    "Some practitioners prefer highly diversified portfolios.",
                    "Others prefer a very small focused universe.",
                ],
                "low_confidence_items": ["Intraday alpha persistence for beginners."],
            }
        )

        self.assertEqual(result.evidence_strength, "strong")
        self.assertTrue(result.conflicts)
        self.assertIn("Intraday alpha persistence for beginners.", result.low_confidence_items)
        source_types = {item.source_type for item in result.standardized_evidence}
        self.assertIn("academic_reference", source_types)
        self.assertIn("regulatory_guidance", source_types)
        self.assertEqual(result.research_findings[0].verification_status, "verified")

    def test_evidence_standardizer_adds_pending_verification_when_references_missing(self):
        standardizer = EvidenceStandardizer()

        result = standardizer.standardize_llm_research_result(
            {
                "core_concepts": ["Backtests should include costs."],
            }
        )

        self.assertIn("Research lacks verifiable public references", result.pending_verification_items)
        self.assertEqual(result.evidence_strength, "weak")

    def test_beginner_candidate_selector_downgrades_candidates_without_explanation_or_data(self):
        selector = BeginnerCandidateSelector()
        framework_artifact = selector.build_candidate_artifact(
            rows=[
                {
                    "symbol": "NVDA.US",
                    "market": "us",
                    "name": "NVIDIA",
                    "rationale": "AI leader with large-cap liquidity",
                    "risk": "valuation sensitivity",
                    "raw_score": 0.92,
                    "signals": [{"score": 0.90, "summary": "trend intact"}],
                    "action_hint": "observe pullbacks",
                },
                {
                    "symbol": "AMD.US",
                    "market": "us",
                    "name": "AMD",
                    "risk": "wide spread and illiquid intraday tape",
                    "raw_score": 0.88,
                },
            ],
            preferred_markets=["us"],
            max_candidates=5,
        )

        observations = {item.symbol: item for item in framework_artifact.candidate_observations}
        self.assertEqual(observations["NVDA.US"].selected_as, "observe_only")
        self.assertTrue(observations["NVDA.US"].meta["needs_llm_research"])
        self.assertEqual(observations["AMD.US"].selected_as, "validate_only")
        self.assertGreaterEqual(framework_artifact.meta["framework_summary"]["llm_research_pending_count"], 1)

    def test_candidate_preparation_service_rewrites_payload_with_metadata(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runs = tmp_path / "state" / "runs"
            runs.mkdir(parents=True)
            (runs / "candidate_inputs.dynamic.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "symbol": "700.HK",
                                "market": "hong_kong",
                                "name": "Tencent",
                                "rationale": "Platform cash flow and buyback support remain intact.",
                                "risk": "regulation overhang",
                                "raw_score": 0.82,
                                "action_hint": "review on pullbacks",
                                "signals": [{"score": 0.78, "summary": "trend intact"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runs / "candidate_inputs.json").write_text(
                json.dumps(
                    [
                        {
                            "symbol": "NVDA.US",
                            "market": "us",
                            "name": "NVIDIA",
                            "rationale": "AI demand remains strong.",
                            "risk": "valuation sensitivity",
                            "raw_score": 0.88,
                            "action_hint": "buy on pullback",
                            "signals": [{"score": 0.9, "summary": "leadership intact"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            report = CandidateInputPreparationService(tmp_path).prepare(
                as_of_date="2026-05-10",
                include_market_data=False,
            )

            self.assertTrue(Path(report["report_path"]).exists())
            self.assertEqual(report["summary"]["market_coverage"], ["hong_kong", "us"])
            self.assertIn("static", report["summary"]["written_targets"])
            self.assertIn("dynamic", report["summary"]["written_targets"])
            self.assertFalse(report["summary"]["include_market_data"])
            self.assertEqual(report["summary"]["knot_runtime"], "auto")

            dynamic_payload = json.loads((runs / "candidate_inputs.dynamic.json").read_text(encoding="utf-8"))
            static_payload = json.loads((runs / "candidate_inputs.json").read_text(encoding="utf-8"))
            self.assertEqual(dynamic_payload["schema_version"], "candidate_inputs_v3")
            self.assertEqual(static_payload["schema_version"], "candidate_inputs_v3")
            self.assertTrue(dynamic_payload["generated_at"].endswith("+08:00"))
            self.assertEqual(datetime.fromisoformat(dynamic_payload["generated_at"]).utcoffset().total_seconds(), 8 * 3600)
            self.assertEqual(dynamic_payload["items"][0]["symbol"], "00700.HK")
            self.assertTrue(dynamic_payload["items"][0]["explanation_ready"])
            self.assertEqual(dynamic_payload["items"][0]["candidate_type"], "dynamic")
            self.assertEqual(static_payload["items"][0]["theme_bucket"], "ai_compute")
            self.assertIn("scoring_model", dynamic_payload)
            self.assertIn("enrichment", dynamic_payload)
            self.assertEqual(dynamic_payload["preparation_metadata"]["knot_runtime"], "auto")
            self.assertGreaterEqual(static_payload["items"][0]["data_completeness"], 0.85)

    def test_candidate_preparation_service_writes_strict_json_when_payload_contains_nan(self):
        from tempfile import TemporaryDirectory

        class StubGenerationService:
            def generate(self, *, mode: str, **_: object) -> dict[str, object]:
                return {
                    "schema_version": "candidate_inputs_v3",
                    "generated_at": "2026-05-10T22:00:00+08:00",
                    "as_of_date": "2026-05-10",
                    "mode": mode,
                    "selection_policy": f"stub_{mode}",
                    "scoring_model": {"model_id": "stub_model"},
                    "enrichment": {
                        "market_data": {
                            "status": "ok",
                            "nan_ratio": float("nan"),
                        }
                    },
                    "item_count": 1,
                    "market_coverage": ["us"],
                    "market_counts": {"us": 1},
                    "warnings": [],
                    "items": [
                        {
                            "symbol": "NVDA.US",
                            "market": "us",
                            "name": "NVIDIA",
                            "candidate_type": mode,
                            "raw_score": 0.9,
                            "rationale": "AI leadership remains intact.",
                            "risk": "valuation sensitivity",
                            "action_hint": "observe pullbacks",
                            "signals": [{"score": 0.8, "summary": "trend intact"}],
                            "selection_policy": f"stub_{mode}",
                            "strategy_tags": ["ai_compute"],
                            "source_breakdown": {"classic": 1.0},
                            "quote": {
                                "stock_owner": float("nan"),
                                "future_position": float("inf"),
                            },
                        }
                    ],
                }

        def reject_invalid_json_constant(value: str) -> None:
            raise AssertionError(f"Unexpected JSON constant: {value}")

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runs = tmp_path / "state" / "runs"
            runs.mkdir(parents=True)
            (runs / "candidate_inputs.dynamic.json").write_text(json.dumps({"items": [{"symbol": "NVDA.US"}]}), encoding="utf-8")
            (runs / "candidate_inputs.json").write_text(json.dumps({"items": [{"symbol": "NVDA.US"}]}), encoding="utf-8")

            service = CandidateInputPreparationService(tmp_path)
            service.generation_service = StubGenerationService()

            report = service.prepare(as_of_date="2026-05-10", include_market_data=False)

            for path in (
                runs / "candidate_inputs.dynamic.json",
                runs / "candidate_inputs.json",
                runs / "candidate_inputs.prepare.report.json",
            ):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("NaN", text)
                self.assertNotIn("Infinity", text)
                json.loads(text, parse_constant=reject_invalid_json_constant)

            dynamic_payload = json.loads((runs / "candidate_inputs.dynamic.json").read_text(encoding="utf-8"))
            self.assertIsNone(dynamic_payload["items"][0]["quote"]["stock_owner"])
            self.assertIsNone(dynamic_payload["items"][0]["quote"]["future_position"])
            self.assertIsNone(dynamic_payload["enrichment"]["market_data"]["nan_ratio"])
            self.assertIsNone(report["targets"]["dynamic"]["enrichment"]["market_data"]["nan_ratio"])

    def test_quant_workflow_prepare_candidates_adds_prepare_step_and_artifact(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runs = tmp_path / "state" / "runs"
            runs.mkdir(parents=True)
            (runs / "candidate_inputs.json").write_text(
                json.dumps(
                    [
                        {
                            "symbol": "NVDA.US",
                            "market": "us",
                            "name": "NVIDIA",
                            "rationale": "AI leader with large-cap liquidity",
                            "risk": "valuation sensitivity",
                            "raw_score": 0.84,
                            "action_hint": "observe pullbacks",
                            "signals": [{"score": 0.82, "summary": "trend intact"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            classic = runs / "classic_multifactor"
            classic.mkdir(parents=True)
            (classic / "vnpy_cta_backtest_report.json").write_text(
                json.dumps(
                    {
                        "symbol": "NVDA.US",
                        "start": "2024-01-01",
                        "end": "2024-12-31",
                        "stats": {"status": "ok", "sharpe_ratio": 1.1, "return_drawdown_ratio": 1.5, "trade_count": 8},
                    }
                ),
                encoding="utf-8",
            )

            service = QuantWorkflowService(tmp_path)
            result = service.run(
                profile={"preferred_market": "us", "risk_profile": "conservative"},
                preferred_markets=["us"],
                stage="readiness",
                prepare_candidates=True,
                prepare_include_market_data=True,
            )

            self.assertTrue(Path(result["candidate_prepare_report"]).exists())
            self.assertEqual(result["steps"][0]["step"], "candidate_prepare")
            self.assertTrue(any(step["step"] == "candidate_framework" for step in result["steps"]))
            prepare_report = json.loads(Path(result["candidate_prepare_report"]).read_text(encoding="utf-8"))
            self.assertIn("static", prepare_report["summary"]["written_targets"])
            self.assertTrue(prepare_report["summary"]["include_market_data"])
            self.assertEqual(prepare_report["summary"]["knot_runtime"], "auto")
            self.assertTrue(result["steps"][0]["meta"]["include_market_data"])
            self.assertEqual(result["steps"][0]["meta"]["knot_runtime"], "auto")
            self.assertTrue(result["started_at"].endswith("+08:00"))
            self.assertEqual(datetime.fromisoformat(result["started_at"]).utcoffset().total_seconds(), 8 * 3600)
            self.assertIn("candidate_prepare_report", _cli_summary(result, preset="beginner_full")["artifacts"])

    def test_quant_workflow_service_runs_in_plan_mode(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runs = tmp_path / "state" / "runs"
            classic = runs / "classic_multifactor"
            runs.mkdir(parents=True)
            classic.mkdir(parents=True)
            (runs / "candidate_inputs.json").write_text(
                json.dumps(
                    [
                        {
                            "symbol": "NVDA.US",
                            "market": "us",
                            "name": "NVIDIA",
                            "rationale": "AI leader with large-cap liquidity",
                            "risk": "valuation sensitivity",
                            "raw_score": 0.84,
                            "action_hint": "observe pullbacks",
                            "signals": [{"score": 0.82, "summary": "trend intact"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (classic / "vnpy_cta_backtest_report.json").write_text(
                json.dumps(
                    {
                        "symbol": "NVDA.US",
                        "start": "2024-01-01",
                        "end": "2024-12-31",
                        "stats": {"status": "ok", "sharpe_ratio": 1.1, "return_drawdown_ratio": 1.5, "trade_count": 8},
                    }
                ),
                encoding="utf-8",
            )
            (classic / "vnpy_cta_sweep_report.json").write_text(
                json.dumps(
                    {
                        "per_symbol_best": [
                            {
                                "symbol": "NVDA.US",
                                "params": {"fast_window": 10, "slow_window": 60},
                                "stats_summary": {"status": "ok", "sharpe_ratio": 1.1},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = QuantWorkflowService(tmp_path)
            result = service.run(
                profile={"preferred_market": "us", "risk_profile": "conservative"},
                preferred_markets=["us"],
                stage="readiness",
            )

            self.assertIn(result["status"], {"ok", "warning", "blocked"})
            self.assertTrue(Path(result["candidate_artifact"]).exists())
            self.assertTrue(Path(result["backtest_artifact"]).exists())
            self.assertTrue(Path(result["readiness_artifact"]).exists())
            self.assertTrue(Path(result["workflow_report"]).exists())
            self.assertIn("workflow_summary", result)

            step_names = [step["step"] for step in result["steps"]]
            self.assertEqual(step_names, ["healthcheck", "candidate_framework", "backtest", "readiness"])
            candidate_steps = [step for step in result["steps"] if step["step"] == "candidate_framework"]
            self.assertTrue(candidate_steps)
            self.assertEqual(candidate_steps[0]["outputs"], [result["candidate_artifact"]])

            candidate_payload = json.loads(Path(result["candidate_artifact"]).read_text(encoding="utf-8"))
            backtest_payload = json.loads(Path(result["backtest_artifact"]).read_text(encoding="utf-8"))
            workflow_payload = json.loads(Path(result["workflow_report"]).read_text(encoding="utf-8"))
            self.assertIn("artifact_summary", candidate_payload["meta"])
            self.assertIn("traceability", candidate_payload["meta"])
            self.assertIn("risk_labels", candidate_payload["meta"])
            self.assertIn("rendered_formats", candidate_payload["meta"])
            self.assertIn("backtest_results", backtest_payload["meta"])
            self.assertIn("workflow_summary", workflow_payload)
            self.assertIn("traceability", workflow_payload)

    def test_quant_workflow_second_run_refreshes_artifacts_with_new_assumptions(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runs = tmp_path / "state" / "runs"
            classic = runs / "classic_multifactor"
            runs.mkdir(parents=True)
            classic.mkdir(parents=True)
            (runs / "candidate_inputs.json").write_text(
                json.dumps(
                    [
                        {
                            "symbol": "NVDA.US",
                            "market": "us",
                            "name": "NVIDIA",
                            "rationale": "AI leader with large-cap liquidity",
                            "risk": "valuation sensitivity",
                            "raw_score": 0.84,
                            "action_hint": "daily trend review",
                            "signals": [{"score": 0.81, "summary": "trend intact"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (classic / "vnpy_cta_backtest_report.json").write_text(
                json.dumps(
                    {
                        "symbol": "NVDA.US",
                        "start": "2024-01-01",
                        "end": "2024-12-31",
                        "stats": {"status": "ok", "sharpe_ratio": 1.1, "return_drawdown_ratio": 1.5, "trade_count": 8},
                    }
                ),
                encoding="utf-8",
            )
            (classic / "vnpy_cta_sweep_report.json").write_text(
                json.dumps(
                    {
                        "per_symbol_best": [
                            {
                                "symbol": "NVDA.US",
                                "params": {"fast_window": 10, "slow_window": 60},
                                "stats_summary": {"status": "ok", "sharpe_ratio": 1.1},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = QuantWorkflowService(tmp_path)
            first = service.run(
                profile={"preferred_market": "us", "risk_profile": "conservative"},
                preferred_markets=["us"],
                stage="readiness",
            )
            second = service.run(
                profile={"preferred_market": "hong_kong", "risk_profile": "moderate"},
                preferred_markets=["us"],
                stage="readiness",
                task_type="simulation",
            )

            first_readiness = json.loads(Path(first["readiness_artifact"]).read_text(encoding="utf-8"))
            second_readiness = json.loads(Path(second["readiness_artifact"]).read_text(encoding="utf-8"))

            self.assertNotEqual(first["readiness_artifact"], second["readiness_artifact"])
            self.assertEqual(first_readiness["meta"]["task_type"], "simulation")
            self.assertEqual(second_readiness["meta"]["task_type"], "simulation")
            self.assertNotEqual(first_readiness["assumptions"], second_readiness["assumptions"])
            self.assertIn("simulation_acceptance", second_readiness["meta"])
            self.assertIn("live_evidence", second_readiness["meta"])
            self.assertTrue(second_readiness["rendered_documents"])
            self.assertIn("Readiness checkpoints", second_readiness["rendered_documents"][0]["body"])
            self.assertIn("Next actions", second_readiness["rendered_documents"][0]["body"])

    def test_quant_workflow_collects_local_simulation_and_live_evidence(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runs = tmp_path / "state" / "runs"
            reports = runs / "reports"
            classic = runs / "classic_multifactor"
            runs.mkdir(parents=True)
            reports.mkdir(parents=True)
            classic.mkdir(parents=True)
            (runs / "candidate_inputs.json").write_text(
                json.dumps(
                    [
                        {
                            "symbol": "00700.HK",
                            "market": "hong_kong",
                            "name": "Tencent",
                            "rationale": "Platform cash flow and buyback support remain intact.",
                            "risk": "regulation overhang",
                            "raw_score": 0.82,
                            "action_hint": "daily trend review",
                            "signals": [{"score": 0.75, "summary": "buyback support remains firm"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (classic / "vnpy_cta_backtest_report.json").write_text(
                json.dumps(
                    {
                        "symbol": "00700.HK",
                        "start": "2024-01-01",
                        "end": "2024-12-31",
                        "stats": {
                            "status": "ok",
                            "start_date": "2024-01-01",
                            "end_date": "2024-12-31",
                            "sharpe_ratio": 1.1,
                            "return_drawdown_ratio": 1.5,
                            "sample_count": 260,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (classic / "vnpy_cta_sweep_report.json").write_text(
                json.dumps(
                    {
                        "per_symbol_best": [
                            {
                                "symbol": "00700.HK",
                                "params": {"fast_window": 10, "slow_window": 60},
                                "stats_summary": {"status": "ok", "sharpe_ratio": 1.1},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "preflight_20260510.json").write_text(
                json.dumps({"totals": {"fail": 0, "warn": 0, "ok": 8}}),
                encoding="utf-8",
            )
            (reports / "dual_run_diff_20260510.json").write_text(
                json.dumps({"totals": {"fail": 0, "warn": 0, "ok": 8}, "business_keys": {"only_a_total_count": 0, "only_b_total_count": 0}}),
                encoding="utf-8",
            )
            (runs / "hk_live_task_report.json").write_text(
                json.dumps(
                    {
                        "env_var_required": "VNPY_LIVE_SUBMIT",
                        "risk_config": {"approval_env_var_required": "VNPY_LIVE_APPROVED"},
                    }
                ),
                encoding="utf-8",
            )
            (runs / "futu_live_position_reconcile.json").write_text(
                json.dumps({"status": "ok"}),
                encoding="utf-8",
            )

            service = QuantWorkflowService(tmp_path)
            result = service.run(
                profile={"preferred_market": "hong_kong", "risk_profile": "moderate"},
                preferred_markets=["hong_kong"],
                mode="stage_only",
                stage="readiness",
                task_type="live",
            )

            readiness_steps = [step for step in result["steps"] if step["step"] == "readiness"]
            self.assertTrue(readiness_steps)
            self.assertTrue(readiness_steps[0]["meta"]["simulation_acceptance"]["latest_preflight_passed"])
            self.assertTrue(readiness_steps[0]["meta"]["simulation_acceptance"]["latest_diff_passed"])
            self.assertTrue(readiness_steps[0]["meta"]["live_evidence"]["approval_switches_documented"])
            self.assertTrue(readiness_steps[0]["meta"]["live_evidence"]["reconciliation_recent"])

            readiness_payload = json.loads(Path(result["readiness_artifact"]).read_text(encoding="utf-8"))
            self.assertIn("simulation_acceptance", readiness_payload["meta"])
            self.assertIn("live_evidence", readiness_payload["meta"])
            self.assertTrue(readiness_payload["meta"]["simulation_acceptance"]["latest_diff_passed"])
            self.assertTrue(readiness_payload["meta"]["live_evidence"]["report_schema_ready"])

    def test_quant_workflow_healthcheck_only_mode_limits_steps(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runs = tmp_path / "state" / "runs"
            runs.mkdir(parents=True)

            service = QuantWorkflowService(tmp_path)
            result = service.run(mode="healthcheck_only", stage="healthcheck")

            step_names = [step["step"] for step in result["steps"]]
            self.assertEqual(step_names, ["healthcheck"])
            self.assertNotIn("candidate_artifact", result)
            self.assertNotIn("backtest_artifact", result)
            self.assertNotIn("readiness_artifact", result)

    def test_quant_workflow_writes_latest_index_file(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runs = tmp_path / "state" / "runs"
            classic = runs / "classic_multifactor"
            runs.mkdir(parents=True)
            classic.mkdir(parents=True)
            (runs / "candidate_inputs.json").write_text(
                json.dumps(
                    [
                        {
                            "symbol": "NVDA.US",
                            "market": "us",
                            "name": "NVIDIA",
                            "rationale": "AI leader with large-cap liquidity",
                            "risk": "valuation sensitivity",
                            "raw_score": 0.84,
                            "action_hint": "observe pullbacks",
                            "signals": [{"score": 0.82, "summary": "trend intact"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (classic / "vnpy_cta_backtest_report.json").write_text(
                json.dumps(
                    {
                        "symbol": "NVDA.US",
                        "start": "2024-01-01",
                        "end": "2024-12-31",
                        "stats": {"status": "ok", "sharpe_ratio": 1.1, "return_drawdown_ratio": 1.5, "trade_count": 8},
                    }
                ),
                encoding="utf-8",
            )

            service = QuantWorkflowService(tmp_path)
            result = service.run(profile={"preferred_market": "us"}, preferred_markets=["us"], stage="readiness")

            latest_index_path = Path(result["latest_index"])
            self.assertTrue(latest_index_path.exists())
            latest_index = json.loads(latest_index_path.read_text(encoding="utf-8"))
            self.assertIn("artifacts", latest_index)
            self.assertIn("workflow_reports", latest_index)
            self.assertIn("beginner_quant_candidate_framework", latest_index["artifacts"])
            self.assertIn("beginner_quant_backtest", latest_index["artifacts"])
            self.assertIn("beginner_quant_readiness", latest_index["artifacts"])
            self.assertIn("beginner_quant", latest_index["workflow_reports"])

    def test_cli_preset_resolution_uses_preset_defaults(self):
        args = argparse.Namespace(
            workflow=None,
            preset="simulation_readiness",
            mode=None,
            stage=None,
            task_type=None,
        )

        resolved = _resolve_workflow_args(args)

        self.assertEqual(resolved["preset"], "simulation_readiness")
        self.assertEqual(resolved["workflow"], PRESET_WORKFLOWS["simulation_readiness"]["workflow"])
        self.assertEqual(resolved["mode"], PRESET_WORKFLOWS["simulation_readiness"]["mode"])
        self.assertEqual(resolved["stage"], PRESET_WORKFLOWS["simulation_readiness"]["stage"])
        self.assertEqual(resolved["task_type"], PRESET_WORKFLOWS["simulation_readiness"]["task_type"])

    def test_cli_summary_only_keeps_paths_and_summary(self):
        payload = _cli_summary(
            {
                "status": "ok",
                "workflow_name": "beginner_quant",
                "mode": "plan",
                "task_type": "simulation",
                "workflow_summary": {"step_count": 4},
                "workflow_report": "/tmp/workflow.json",
                "latest_index": "/tmp/latest_index.json",
                "candidate_artifact": "/tmp/candidate.json",
                "backtest_artifact": "/tmp/backtest.json",
                "readiness_artifact": "/tmp/readiness.json",
                "candidate_prepare_report": "/tmp/candidate_prepare_report.json",
                "warnings": ["offline placeholder"],
            },
            preset="beginner_full",
        )

        self.assertEqual(payload["preset"], "beginner_full")
        self.assertEqual(payload["workflow_summary"]["step_count"], 4)
        self.assertEqual(payload["artifacts"]["readiness_artifact"], "/tmp/readiness.json")
        self.assertEqual(payload["artifacts"]["candidate_prepare_report"], "/tmp/candidate_prepare_report.json")
        self.assertEqual(payload["latest_index"], "/tmp/latest_index.json")
        self.assertEqual(payload["task_type"], "simulation")

    def test_prepare_related_cli_defaults_use_auto_knot_runtime(self):
        workflow_args = build_quant_workflow_parser().parse_args([])
        prepare_args = build_prepare_candidate_parser().parse_args([])

        self.assertEqual(workflow_args.prepare_knot_runtime, "auto")
        self.assertEqual(prepare_args.knot_runtime, "auto")

    def test_quant_workflow_module_entrypoint_reexports_main(self):
        self.assertTrue(callable(quant_workflow_module.main))

    def test_quant_workflow_stage_only_live_blocks_without_required_artifacts(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            service = QuantWorkflowService(tmp_path)
            result = service.run(mode="stage_only", stage="readiness", task_type="live")

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["steps"][0]["step"], "healthcheck")
            self.assertEqual(result["steps"][0]["status"], "blocked")
            self.assertIn("checks", result["steps"][0]["meta"])
            self.assertIn("blocking_reasons", result["steps"][0]["meta"])
            self.assertIn("candidate_inputs_any", result["steps"][0]["meta"]["checks"])
            self.assertTrue(any("candidate_inputs_any" in warning for warning in result["warnings"]))
            self.assertTrue(any("backtest_report" in warning for warning in result["warnings"]))
            self.assertTrue(Path(result["workflow_report"]).exists())


if __name__ == "__main__":
    unittest.main()