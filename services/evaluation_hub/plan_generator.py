from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .hub import EvaluationHub
from .models import (
    CandidateObservation,
    PlanAssumption,
    PlanPhase,
    PlanningArtifact,
    PlanTask,
    RiskBudget,
)


class BeginnerPlanGenerator:
    def __init__(self, hub: EvaluationHub | None = None):
        self.hub = hub or EvaluationHub()

    def build_plan(
        self,
        *,
        profile: dict[str, Any] | None = None,
        observations: list[CandidateObservation] | None = None,
        generated_at: str | None = None,
        version: str = "v1",
        previous_plan: PlanningArtifact | None = None,
    ) -> PlanningArtifact:
        generated_at = generated_at or datetime.now(timezone.utc).isoformat()
        profile = dict(profile or {})
        assumptions = self._assumptions(profile)
        risk_budget = self._risk_budget(profile)
        phases = self._phases(profile, observations or [], risk_budget)
        execution_suggestions = self._execution_suggestions(profile, observations or [], risk_budget)
        risk_prompts = self._risk_prompts(risk_budget)
        invalidation_conditions = self._invalidation_conditions(profile)
        meta = {
            "profile": profile,
            "plan_differences": self._plan_differences(previous_plan, assumptions, risk_budget) if previous_plan else [],
        }
        return self.hub.build_planning_artifact(
            artifact_type="beginner_quant_plan",
            title="Beginner Quant Personal Plan",
            generated_at=generated_at,
            version=version,
            assumptions=assumptions,
            candidate_observations=list(observations or []),
            plan_phases=phases,
            risk_budget=risk_budget,
            execution_suggestions=execution_suggestions,
            risk_prompts=risk_prompts,
            invalidation_conditions=invalidation_conditions,
            meta=meta,
        )

    def _assumptions(self, profile: dict[str, Any]) -> list[PlanAssumption]:
        capital = profile.get("capital")
        drawdown = profile.get("max_drawdown_pct")
        market = profile.get("preferred_market") or "us"
        available_hours = profile.get("hours_per_week")
        risk_profile = profile.get("risk_profile") or "conservative"
        assumptions = [
            PlanAssumption("preferred_market", str(market), "Use one main market for simpler review and execution alignment."),
            PlanAssumption("risk_profile", str(risk_profile), "Unknown profile defaults to conservative behaviour."),
        ]
        assumptions.append(
            PlanAssumption(
                "capital",
                str(capital if capital not in {None, ""} else "unknown_keep_small"),
                "When capital is missing, use smaller position sizes and fewer names.",
            )
        )
        assumptions.append(
            PlanAssumption(
                "max_drawdown_pct",
                str(drawdown if drawdown not in {None, ""} else 8),
                "Unknown drawdown tolerance defaults to a stricter pause threshold.",
            )
        )
        assumptions.append(
            PlanAssumption(
                "hours_per_week",
                str(available_hours if available_hours not in {None, ""} else 5),
                "Unknown time availability defaults to a low-frequency plan.",
            )
        )
        return assumptions

    def _risk_budget(self, profile: dict[str, Any]) -> RiskBudget:
        risk_profile = str(profile.get("risk_profile") or "conservative").lower()
        if risk_profile == "moderate":
            return RiskBudget(
                single_position_limit_pct=0.08,
                total_exposure_limit_pct=0.35,
                max_positions=4,
                sector_limit_pct=0.20,
                daily_new_risk_budget_pct=0.12,
                stop_conditions=[
                    "Pause new entries after three consecutive losses.",
                    "Return to review mode if portfolio drawdown exceeds 8%.",
                    "Pause the workflow if quote/data anomalies are unresolved.",
                ],
            )
        return RiskBudget(
            single_position_limit_pct=0.05,
            total_exposure_limit_pct=0.25,
            max_positions=3,
            sector_limit_pct=0.15,
            daily_new_risk_budget_pct=0.08,
            stop_conditions=[
                "Pause new entries after two consecutive losses.",
                "Return to review mode if portfolio drawdown exceeds 6%.",
                "Stop execution steps if reconciliation, logging or data checks are not current.",
            ],
        )

    def _phases(self, profile: dict[str, Any], observations: list[CandidateObservation], risk_budget: RiskBudget) -> list[PlanPhase]:
        watchlist_symbols = [row.symbol for row in observations if row.selected_as == "beginner_watchlist"][:3]
        review_symbols = watchlist_symbols or [row.symbol for row in observations[:3]]
        return [
            PlanPhase(
                name="Preparation",
                goal="Understand system boundaries and define a small review universe.",
                duration_hint="1-2 weeks",
                tasks=[
                    PlanTask(
                        period="daily",
                        title="Read-only system check",
                        actions=[
                            "Run health checks or review the latest health report.",
                            "Keep a short note of any blocked or missing local capability.",
                        ],
                        review_points=["No execution-stage blockers remain unexplained."],
                        pause_conditions=["If health status is blocked, stay in research mode."],
                    ),
                    PlanTask(
                        period="weekly",
                        title="Watchlist definition",
                        actions=[
                            f"Keep the active review list to a few names: {', '.join(review_symbols) if review_symbols else 'choose up to 3 names'}.",
                            "Write one sentence for why each name is on the list and one sentence for why it should be removed.",
                        ],
                        review_points=["Each name has a thesis and an invalidation condition."],
                        pause_conditions=["If the watchlist grows beyond what you can review manually, cut it back."],
                    ),
                ],
            ),
            PlanPhase(
                name="Research",
                goal="Build evidence-based understanding before touching execution paths.",
                duration_hint="2-4 weeks",
                tasks=[
                    PlanTask(
                        period="daily",
                        title="Evidence review",
                        actions=[
                            "Review one research section and rewrite it in your own words.",
                            "Mark every low-confidence item as to_verify rather than treating it as a fact.",
                        ],
                        review_points=["You can explain the strategy idea and its failure conditions without jargon."],
                        pause_conditions=["If key evidence cannot be verified, do not promote the idea to a trading rule."],
                    ),
                    PlanTask(
                        period="weekly",
                        title="Candidate explanation",
                        actions=[
                            "Review candidate observations and classify them into watchlist / observe_only / validate_only.",
                            "Check whether multiple names are actually the same theme exposure.",
                        ],
                        review_points=["No more than two highly correlated names dominate the watchlist."],
                        pause_conditions=["If volatility or liquidity assumptions are unclear, downgrade the name to observe_only."],
                    ),
                ],
            ),
            PlanPhase(
                name="Backtest",
                goal="Validate assumptions with explicit cost and data-quality notes.",
                duration_hint="2-3 weeks",
                tasks=[
                    PlanTask(
                        period="weekly",
                        title="Backtest review",
                        actions=[
                            "Run or review one backtest with clear sample period, fees and slippage assumptions.",
                            "Compare return, drawdown, turnover and stability rather than only headline return.",
                        ],
                        review_points=["At least one out-of-sample or alternative-regime check exists."],
                        pause_conditions=["If the result only works after heavy parameter tuning, send it back to research."],
                    ),
                    PlanTask(
                        period="monthly",
                        title="Robustness check",
                        actions=[
                            "Record what changed between parameter versions.",
                            "Reject upgrades when data-quality or microstructure assumptions are missing.",
                        ],
                        review_points=["The strategy remains understandable after each parameter change."],
                        pause_conditions=["If transaction costs dominate the edge, do not move to simulation."],
                    ),
                ],
            ),
            PlanPhase(
                name="Simulation",
                goal="Practice the workflow with explicit risk budget and stop rules.",
                duration_hint="4-6 weeks",
                tasks=[
                    PlanTask(
                        period="daily",
                        title="Small simulation routine",
                        actions=[
                            "Use only a few names from the review universe and keep the session explainable.",
                            f"Respect the position budget: max {risk_budget.max_positions} names, {risk_budget.single_position_limit_pct:.0%} per name, {risk_budget.total_exposure_limit_pct:.0%} total exposure.",
                        ],
                        review_points=["Every action can be traced back to a review note or a rule.", "Pause rules are checked before new entries."],
                        pause_conditions=list(risk_budget.stop_conditions),
                    ),
                    PlanTask(
                        period="weekly",
                        title="Simulation recap",
                        actions=[
                            "Review whether the names stayed within the intended theme and risk limits.",
                            "Document execution anomalies, missing data, and what should be improved before another run.",
                        ],
                        review_points=["At least one full recap note exists for each simulation week."],
                        pause_conditions=["If anomalies repeat without a fix, stop and return to preparation or research."],
                    ),
                ],
            ),
            PlanPhase(
                name="Review",
                goal="Keep the plan iterative and explicit about what changed.",
                duration_hint="ongoing",
                tasks=[
                    PlanTask(
                        period="monthly",
                        title="Version review",
                        actions=[
                            "Compare the current assumptions with the prior version before changing the workflow.",
                            "Only widen the universe or increase complexity when the current routine is stable and documented.",
                        ],
                        review_points=["The reason for each plan change is written down."],
                        pause_conditions=["If you are changing too many variables at once, simplify the workflow again."],
                    )
                ],
            ),
        ]

    def _execution_suggestions(self, profile: dict[str, Any], observations: list[CandidateObservation], risk_budget: RiskBudget) -> list[str]:
        suggestions = [
            "Default to research and backtest review before any simulation workflow.",
            f"Keep active names to {risk_budget.max_positions} or fewer and prefer low-frequency reviewable setups.",
            "Use observation-only names when volatility, liquidity or system support is not clear enough.",
        ]
        if observations:
            top_watchlist = [row.symbol for row in observations if row.selected_as == "beginner_watchlist"][:3]
            if top_watchlist:
                suggestions.append("Begin the first tracking cycle with: " + ", ".join(top_watchlist))
        preferred_market = profile.get("preferred_market")
        if preferred_market:
            suggestions.append(f"Stay focused on the preferred market ({preferred_market}) until the routine is stable.")
        return suggestions

    def _risk_prompts(self, risk_budget: RiskBudget) -> list[str]:
        return [
            f"Single-position limit: {risk_budget.single_position_limit_pct:.0%}.",
            f"Total exposure limit: {risk_budget.total_exposure_limit_pct:.0%}.",
            f"Max positions: {risk_budget.max_positions}.",
            f"Sector/theme concentration limit: {risk_budget.sector_limit_pct:.0%}.",
            f"Daily new risk budget: {risk_budget.daily_new_risk_budget_pct:.0%}.",
        ]

    def _invalidation_conditions(self, profile: dict[str, Any]) -> list[str]:
        return [
            "If the user cannot maintain regular review notes, the plan should fall back to a simpler low-frequency routine.",
            "If the local system cannot support the requested stage safely, keep the plan in research/backtest/simulation mode.",
            "If drawdown tolerance, available time or market preference changes, regenerate only the affected plan sections and compare differences.",
        ]

    def _plan_differences(
        self,
        previous_plan: PlanningArtifact,
        assumptions: list[PlanAssumption],
        risk_budget: RiskBudget,
    ) -> list[dict[str, Any]]:
        previous_assumptions = {item.name: item.value for item in previous_plan.assumptions}
        changes: list[dict[str, Any]] = []
        for item in assumptions:
            if previous_assumptions.get(item.name) != item.value:
                changes.append({"field": item.name, "old": previous_assumptions.get(item.name), "new": item.value})
        previous_budget = previous_plan.risk_budget
        if previous_budget is not None:
            current_budget = asdict(risk_budget)
            for key, value in current_budget.items():
                if getattr(previous_budget, key) != value:
                    changes.append({"field": f"risk_budget.{key}", "old": getattr(previous_budget, key), "new": value})
        return changes
