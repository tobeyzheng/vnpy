from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.evaluation_hub import CapabilityRegistry
from services.evaluation_hub.candidate_framework import BeginnerCandidateFramework
from services.evaluation_hub.doc_renderer import BeginnerExplanationRenderer
from services.evaluation_hub.plan_generator import BeginnerPlanGenerator
from services.evaluation_hub.readiness_gate import ReadinessGateService
from scripts.quant_workflow.workflow_service import QuantWorkflowService


class BeginnerQuantWorkflowTests(unittest.TestCase):
    def test_capability_registry_exposes_known_gaps(self):
        registry = CapabilityRegistry()

        gaps = registry.capability_gaps()

        self.assertTrue(any(gap.capability_id == "gap.hk_sim_task" for gap in gaps))
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
        self.assertIn("### Next actions", markdown)
        self.assertTrue(any("preferred_market" in item for item in payload["plan_differences"]))
        self.assertIn("Resolve readiness failures before upgrading to the next stage.", payload["next_actions"])

    def test_quant_workflow_service_runs_in_plan_mode(self):
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
                        }
                    ]
                ),
                encoding="utf-8",
            )

            service = QuantWorkflowService(tmp_path)
            result = service.run(profile={"preferred_market": "us", "risk_profile": "conservative"}, preferred_markets=["us"], stage="research")

            self.assertIn(result["status"], {"ok", "blocked"})
            self.assertTrue(Path(result["research_artifact"]).exists())
            self.assertTrue(Path(result["plan_artifact"]).exists())
            self.assertTrue(Path(result["workflow_report"]).exists())
            self.assertTrue(any(step["step"] == "execution_boundary" for step in result["steps"]))

    def test_quant_workflow_second_run_loads_previous_plan_and_records_differences(self):
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
                        }
                    ]
                ),
                encoding="utf-8",
            )

            service = QuantWorkflowService(tmp_path)
            first = service.run(profile={"preferred_market": "us", "risk_profile": "conservative"}, preferred_markets=["us"], stage="research")
            second = service.run(profile={"preferred_market": "hong_kong", "risk_profile": "moderate"}, preferred_markets=["us"], stage="research")

            first_plan = json.loads(Path(first["plan_artifact"]).read_text(encoding="utf-8"))
            second_plan = json.loads(Path(second["plan_artifact"]).read_text(encoding="utf-8"))
            planning_steps = [step for step in second["steps"] if step["step"] == "planning"]

            self.assertTrue(second_plan["meta"]["previous_plan_loaded"])
            self.assertTrue(any(item["field"] == "preferred_market" for item in second_plan["meta"].get("plan_differences", [])))
            self.assertNotEqual(first["plan_artifact"], second["plan_artifact"])
            self.assertTrue(planning_steps[0]["meta"]["previous_plan_loaded"])
            self.assertTrue(any(item["field"] == "preferred_market" for item in planning_steps[0]["meta"]["plan_differences"]))
            self.assertTrue(second_plan["rendered_documents"])
            self.assertIn("What changed from the previous plan", second_plan["rendered_documents"][0]["body"])
            self.assertIn("Next actions", second_plan["rendered_documents"][0]["body"])

    def test_quant_workflow_research_only_mode_limits_steps(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runs = tmp_path / "state" / "runs"
            runs.mkdir(parents=True)

            service = QuantWorkflowService(tmp_path)
            result = service.run(mode="research_only", stage="research")

            step_names = [step["step"] for step in result["steps"]]
            self.assertIn("preflight", step_names)
            self.assertIn("healthcheck", step_names)
            self.assertIn("capability_map", step_names)
            self.assertIn("research", step_names)
            self.assertNotIn("candidate_framework", step_names)
            self.assertNotIn("planning", step_names)
            self.assertIn("research_artifact", result)
            self.assertNotIn("plan_artifact", result)

    def test_quant_workflow_stage_only_live_blocks_without_required_artifacts(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            service = QuantWorkflowService(tmp_path)
            result = service.run(mode="stage_only", stage="live")

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["steps"][0]["step"], "preflight")
            self.assertEqual(result["steps"][0]["status"], "blocked")
            self.assertTrue(any("Candidate inputs are required" in warning for warning in result["warnings"]))
            self.assertTrue(any("backtest report is required" in warning for warning in result["warnings"]))
            self.assertTrue(Path(result["workflow_report"]).exists())


if __name__ == "__main__":
    unittest.main()