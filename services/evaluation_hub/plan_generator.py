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
        profile = self._profile_snapshot(profile or {})
        assumptions = self._assumptions(profile)
        risk_budget = self._risk_budget(profile)
        phases = self._phases(profile, observations or [], risk_budget)
        execution_suggestions = self._execution_suggestions(profile, observations or [], risk_budget)
        risk_prompts = self._risk_prompts(risk_budget)
        invalidation_conditions = self._invalidation_conditions(profile)
        meta = {
            "profile": profile,
            "plan_differences": self._plan_differences(previous_plan, assumptions, risk_budget) if previous_plan else [],
            "personalization_summary": self._personalization_summary(profile, risk_budget),
            "profile_update_scope": self._profile_update_scope(previous_plan, profile),
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
        available_hours = float(profile.get("hours_per_week") or 5)
        max_drawdown = float(profile.get("max_drawdown_pct") or 8)
        capital_bucket = str(profile.get("capital_bucket") or "small")
        cadence = str(profile.get("preferred_cadence") or "low_frequency")

        budget = {
            "single_position_limit_pct": 0.05,
            "total_exposure_limit_pct": 0.25,
            "max_positions": 3,
            "sector_limit_pct": 0.15,
            "daily_new_risk_budget_pct": 0.08,
        }

        if risk_profile == "moderate":
            budget.update(
                {
                    "single_position_limit_pct": 0.07,
                    "total_exposure_limit_pct": 0.32,
                    "max_positions": 4,
                    "sector_limit_pct": 0.18,
                    "daily_new_risk_budget_pct": 0.10,
                }
            )

        if capital_bucket == "micro":
            budget.update(
                {
                    "single_position_limit_pct": min(budget["single_position_limit_pct"], 0.04),
                    "total_exposure_limit_pct": min(budget["total_exposure_limit_pct"], 0.20),
                    "max_positions": min(budget["max_positions"], 2),
                    "daily_new_risk_budget_pct": min(budget["daily_new_risk_budget_pct"], 0.06),
                }
            )
        elif capital_bucket == "medium":
            budget["total_exposure_limit_pct"] = min(budget["total_exposure_limit_pct"] + 0.03, 0.38)
            budget["sector_limit_pct"] = min(budget["sector_limit_pct"] + 0.02, 0.22)
        elif capital_bucket == "large":
            budget["total_exposure_limit_pct"] = min(budget["total_exposure_limit_pct"] + 0.05, 0.40)
            budget["sector_limit_pct"] = min(budget["sector_limit_pct"] + 0.03, 0.24)

        if available_hours < 4:
            budget["max_positions"] = min(budget["max_positions"], 2)
            budget["daily_new_risk_budget_pct"] = min(budget["daily_new_risk_budget_pct"], 0.06)
            budget["total_exposure_limit_pct"] = min(budget["total_exposure_limit_pct"], 0.22)
        elif available_hours >= 10:
            budget["max_positions"] = min(budget["max_positions"] + 1, 5)
            budget["daily_new_risk_budget_pct"] = min(budget["daily_new_risk_budget_pct"] + 0.02, 0.12)

        if max_drawdown <= 6:
            budget["single_position_limit_pct"] = min(budget["single_position_limit_pct"], 0.05)
            budget["total_exposure_limit_pct"] = min(budget["total_exposure_limit_pct"], 0.24)
            budget["sector_limit_pct"] = min(budget["sector_limit_pct"], 0.15)
            budget["daily_new_risk_budget_pct"] = min(budget["daily_new_risk_budget_pct"], 0.06)
        elif max_drawdown >= 12:
            budget["single_position_limit_pct"] = min(budget["single_position_limit_pct"] + 0.01, 0.08)
            budget["total_exposure_limit_pct"] = min(budget["total_exposure_limit_pct"] + 0.04, 0.40)

        if cadence != "low_frequency":
            budget["single_position_limit_pct"] = min(budget["single_position_limit_pct"], 0.04)
            budget["total_exposure_limit_pct"] = min(budget["total_exposure_limit_pct"], 0.18)
            budget["max_positions"] = min(budget["max_positions"], 2)
            budget["sector_limit_pct"] = min(budget["sector_limit_pct"], 0.12)
            budget["daily_new_risk_budget_pct"] = min(budget["daily_new_risk_budget_pct"], 0.05)

        stop_conditions = [
            f"Return to review mode if portfolio drawdown exceeds {max(4, int(round(max_drawdown)))}%.",
            "Pause the workflow if quote/data anomalies are unresolved.",
            "Stop execution steps if reconciliation, logging or data checks are not current.",
        ]
        if risk_profile == "moderate" and max_drawdown > 8:
            stop_conditions.insert(0, "Pause new entries after three consecutive losses.")
        else:
            stop_conditions.insert(0, "Pause new entries after two consecutive losses.")
        if available_hours < 4:
            stop_conditions.append("If review notes are skipped for a week, shrink the active universe before adding new risk.")
        if cadence != "low_frequency":
            stop_conditions.append("If turnover or execution sensitivity rises, downgrade the plan back to low-frequency review mode.")

        return RiskBudget(
            single_position_limit_pct=budget["single_position_limit_pct"],
            total_exposure_limit_pct=budget["total_exposure_limit_pct"],
            max_positions=int(budget["max_positions"]),
            sector_limit_pct=budget["sector_limit_pct"],
            daily_new_risk_budget_pct=budget["daily_new_risk_budget_pct"],
            stop_conditions=stop_conditions,
            pause_conditions=[
                "Pause adding symbols when multiple names map to the same theme exposure.",
                "Pause any stage upgrade when current review notes are incomplete.",
            ],
            review_conditions=[
                "Review position sizing whenever capital, schedule, or drawdown tolerance changes.",
                "Re-check whether the current plan still fits a low-frequency beginner workflow.",
            ],
        )

    def _phases(self, profile: dict[str, Any], observations: list[CandidateObservation], risk_budget: RiskBudget) -> list[PlanPhase]:
        watchlist_symbols = [row.symbol for row in observations if row.selected_as == "beginner_watchlist"][:3]
        review_symbols = watchlist_symbols or [row.symbol for row in observations[:3]]
        available_hours = float(profile.get("hours_per_week") or 5)
        cadence = str(profile.get("preferred_cadence") or "low_frequency")
        time_bucket = str(profile.get("time_budget_bucket") or "steady")
        cadence_note = "Keep the workflow low-frequency and reviewable." if cadence == "low_frequency" else "Treat faster cadence ideas as research or validation tasks unless execution assumptions are explicit."
        prep_duration = "2-3 weeks" if time_bucket == "limited" else "1-2 weeks"
        research_duration = "3-5 weeks" if time_bucket == "limited" else ("2-4 weeks" if time_bucket == "steady" else "2-3 weeks")
        backtest_duration = "3-4 weeks" if cadence != "low_frequency" else ("2-4 weeks" if time_bucket == "limited" else "2-3 weeks")
        simulation_duration = "5-8 weeks" if cadence != "low_frequency" else ("4-6 weeks" if time_bucket != "limited" else "6-8 weeks")
        daily_research_action = (
            "Review one short research note and rewrite only the most important claim in plain language."
            if available_hours < 4
            else "Review one research section and rewrite it in your own words."
        )
        weekly_watchlist_action = (
            f"Keep the active review list to no more than {min(risk_budget.max_positions, 2)} names: {', '.join(review_symbols[:2]) if review_symbols else 'choose up to 2 names'}."
            if available_hours < 4
            else f"Keep the active review list to a few names: {', '.join(review_symbols) if review_symbols else 'choose up to 3 names'}."
        )
        backtest_scope_action = (
            "Review one backtest slowly and focus on assumptions, not trade count."
            if available_hours < 4
            else "Run or review one backtest with clear sample period, fees and slippage assumptions."
        )
        simulation_scope_action = (
            "Use only one or two names from the review universe and keep the session explainable."
            if cadence != "low_frequency" or available_hours < 4
            else "Use only a few names from the review universe and keep the session explainable."
        )
        return [
            PlanPhase(
                name="Preparation",
                goal="Understand system boundaries and define a small review universe.",
                duration_hint=prep_duration,
                tasks=[
                    PlanTask(
                        period="daily",
                        title="Read-only system check",
                        actions=[
                            "Run health checks or review the latest health report.",
                            "Keep a short note of any blocked or missing local capability.",
                            cadence_note,
                        ],
                        review_points=["No execution-stage blockers remain unexplained."],
                        pause_conditions=["If health status is blocked, stay in research mode."],
                    ),
                    PlanTask(
                        period="weekly",
                        title="Watchlist definition",
                        actions=[
                            weekly_watchlist_action,
                            "Write one sentence for why each name is on the list and one sentence for why it should be removed.",
                            "If your current ability is limited, split each blocked step into a learning task and a validation task before moving on.",
                        ],
                        review_points=["Each name has a thesis and an invalidation condition."],
                        pause_conditions=["If the watchlist grows beyond what you can review manually, cut it back."],
                    ),
                ],
            ),
            PlanPhase(
                name="Research",
                goal="Build evidence-based understanding before touching execution paths.",
                duration_hint=research_duration,
                tasks=[
                    PlanTask(
                        period="daily",
                        title="Evidence review",
                        actions=[
                            daily_research_action,
                            "Mark every low-confidence item as to_verify rather than treating it as a fact.",
                            "If a concept is still unclear, rewrite only that chapter instead of regenerating the full plan.",
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
                            "For each name, note whether the current data is enough for research only, observation, or simulation planning.",
                        ],
                        review_points=["No more than two highly correlated names dominate the watchlist."],
                        pause_conditions=["If volatility or liquidity assumptions are unclear, downgrade the name to observe_only."],
                    ),
                ],
            ),
            PlanPhase(
                name="Backtest",
                goal="Validate assumptions with explicit cost and data-quality notes.",
                duration_hint=backtest_duration,
                tasks=[
                    PlanTask(
                        period="weekly",
                        title="Backtest review",
                        actions=[
                            backtest_scope_action,
                            "Compare return, drawdown, turnover and stability rather than only headline return.",
                            "If the workflow is faster than daily, add extra notes for latency, slippage, and execution sensitivity.",
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
                            "Document whether the current plan update changed sizing, schedule, or scope only, and avoid rewriting stable sections without need.",
                        ],
                        review_points=["The strategy remains understandable after each parameter change."],
                        pause_conditions=["If transaction costs dominate the edge, do not move to simulation."],
                    ),
                ],
            ),
            PlanPhase(
                name="Simulation",
                goal="Practice the workflow with explicit risk budget and stop rules.",
                duration_hint=simulation_duration,
                tasks=[
                    PlanTask(
                        period="daily",
                        title="Small simulation routine",
                        actions=[
                            simulation_scope_action,
                            f"Respect the position budget: max {risk_budget.max_positions} names, {risk_budget.single_position_limit_pct:.0%} per name, {risk_budget.total_exposure_limit_pct:.0%} total exposure.",
                            f"Keep daily new risk within {risk_budget.daily_new_risk_budget_pct:.0%} and sector concentration within {risk_budget.sector_limit_pct:.0%}.",
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
                            "If your available time changed, regenerate only the affected sizing and cadence sections and compare differences.",
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
                            "When capital, time, or drawdown tolerance changes, update the affected sections first and keep unrelated sections stable.",
                        ],
                        review_points=["The reason for each plan change is written down."],
                        pause_conditions=["If you are changing too many variables at once, simplify the workflow again."],
                    )
                ],
            ),
        ]

    def _profile_snapshot(self, raw_profile: dict[str, Any]) -> dict[str, Any]:
        profile = dict(raw_profile)
        capital = self._as_float(profile.get("capital"))
        hours = self._as_float(profile.get("hours_per_week"))
        drawdown = self._as_float(profile.get("max_drawdown_pct"))
        preferred_market = str(profile.get("preferred_market") or "us")
        risk_profile = str(profile.get("risk_profile") or "conservative").lower()
        cadence = str(profile.get("preferred_cadence") or "low_frequency").lower()
        if cadence not in {"low_frequency", "swing", "intraday"}:
            cadence = "low_frequency"
        profile["preferred_market"] = preferred_market
        profile["risk_profile"] = risk_profile
        profile["preferred_cadence"] = cadence
        profile["capital"] = capital if capital is not None else "unknown_keep_small"
        profile["hours_per_week"] = hours if hours is not None else 5.0
        profile["max_drawdown_pct"] = drawdown if drawdown is not None else 8.0
        profile["capital_bucket"] = self._capital_bucket(capital)
        profile["time_budget_bucket"] = self._time_budget_bucket(profile["hours_per_week"])
        return profile

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
        if profile.get("preferred_cadence") != "low_frequency":
            suggestions.append("Keep faster-than-daily ideas in research or validation mode until execution sensitivity is explicitly reviewed.")
        if float(profile.get("hours_per_week") or 5) < 4:
            suggestions.append("Use a smaller review universe and fewer active positions because the weekly review window is limited.")
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

    def _personalization_summary(self, profile: dict[str, Any], risk_budget: RiskBudget) -> dict[str, Any]:
        return {
            "capital_bucket": profile.get("capital_bucket"),
            "time_budget_bucket": profile.get("time_budget_bucket"),
            "preferred_cadence": profile.get("preferred_cadence"),
            "max_drawdown_pct": profile.get("max_drawdown_pct"),
            "active_position_limit": risk_budget.max_positions,
            "review_style": "compact" if str(profile.get("time_budget_bucket")) == "limited" else "standard",
        }

    def _profile_update_scope(self, previous_plan: PlanningArtifact | None, profile: dict[str, Any]) -> list[str]:
        if previous_plan is None:
            return ["full_plan_initialization"]
        previous_profile = dict(previous_plan.meta.get("profile") or {})
        changed_fields = {
            key
            for key in ("preferred_market", "risk_profile", "capital", "hours_per_week", "max_drawdown_pct", "preferred_cadence")
            if previous_profile.get(key) != profile.get(key)
        }
        scopes: list[str] = []
        if changed_fields & {"capital", "risk_profile", "max_drawdown_pct"}:
            scopes.append("risk_budget")
        if changed_fields & {"hours_per_week", "preferred_cadence"}:
            scopes.append("phase_schedule")
        if changed_fields & {"preferred_market"}:
            scopes.append("candidate_scope")
        if not scopes:
            scopes.append("no_material_profile_change")
        return scopes

    def _capital_bucket(self, capital: float | None) -> str:
        if capital is None:
            return "small"
        if capital < 10000:
            return "micro"
        if capital < 50000:
            return "small"
        if capital < 200000:
            return "medium"
        return "large"

    def _time_budget_bucket(self, hours_per_week: float) -> str:
        if hours_per_week < 4:
            return "limited"
        if hours_per_week < 10:
            return "steady"
        return "deep"

    def _as_float(self, value: Any) -> float | None:
        try:
            if value in {None, ""}:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
