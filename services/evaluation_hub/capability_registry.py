from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Sequence

from .models import CapabilityDefinition, CapabilityGap


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: list[CapabilityDefinition] = self._default_items()

    def list_all(self) -> list[CapabilityDefinition]:
        return list(self._items)

    def by_stage(self, stage: str) -> list[CapabilityDefinition]:
        return [item for item in self._items if item.stage == stage]

    def by_prefix(self, path_prefix: str) -> list[CapabilityDefinition]:
        prefix = path_prefix.rstrip("/")
        return [item for item in self._items if item.path.startswith(prefix)]

    def get(self, capability_id: str) -> CapabilityDefinition | None:
        for item in self._items:
            if item.capability_id == capability_id:
                return item
        return None

    def select_for_stage(self, stage: str, *, include_unsafe: bool = True) -> list[CapabilityDefinition]:
        items = self.by_stage(stage)
        if include_unsafe:
            return items
        return [item for item in items if item.safe_by_default]

    def beginner_safe_capabilities(self) -> list[CapabilityDefinition]:
        return [item for item in self._items if item.safe_by_default]

    def capability_gaps(self) -> list[CapabilityGap]:
        gaps: list[CapabilityGap] = []
        for item in self._items:
            if item.capability_id.startswith("gap."):
                gaps.append(
                    CapabilityGap(
                        capability_id=item.capability_id,
                        expected_path=item.path,
                        reason=item.description,
                        manual_alternative=item.manual_checkpoints[0] if item.manual_checkpoints else "",
                        suggested_next_step="Add a dedicated entry or keep a manual alternative in the workflow.",
                    )
                )
        return gaps

    def stage_boundary_map(self) -> dict[str, list[dict[str, object]]]:
        boundaries: dict[str, list[dict[str, object]]] = {}
        for stage in ["research", "backtest", "simulation", "live", "orchestration"]:
            boundaries[stage] = [
                {
                    "capability_id": item.capability_id,
                    "path": item.path,
                    "requires_confirmation": item.requires_confirmation,
                    "mutates_state": item.mutates_state,
                    "connects_remote": item.connects_remote,
                    "safe_by_default": item.safe_by_default,
                    "manual_checkpoints": list(item.manual_checkpoints),
                    "inputs": list(item.inputs),
                    "outputs": list(item.outputs),
                }
                for item in self.by_stage(stage)
            ]
        return boundaries

    def to_dict(self) -> list[dict]:
        return [asdict(item) for item in self._items]

    def _default_items(self) -> list[CapabilityDefinition]:
        return [
            CapabilityDefinition(
                capability_id="classic_multifactor.llm_research",
                path="scripts/classic_multifactor/run_llm_research.py",
                stage="research",
                description="Single-topic public-web quant research artifact generator.",
                inputs=["llm config path", "topic", "context", "symbol", "timeframe", "schema preset"],
                outputs=["state/runs/llm_research/*.json"],
                manual_checkpoints=["Review returned references and evidence level before using conclusions."],
                requires_confirmation=False,
                mutates_state=False,
                connects_remote=True,
                safe_by_default=True,
            ),
            CapabilityDefinition(
                capability_id="classic_multifactor.intraday_runner",
                path="scripts/classic_multifactor/run_intraday_loop.py",
                stage="simulation",
                description="Minute-level mainline runner with four-stage execution guards and dry-run by default.",
                inputs=["classic config json", "session window", "live-submit switches"],
                outputs=["state/runs/classic_multifactor_intraday_report.json", "state/runs/events.jsonl", "state/runs/orders/*.json"],
                manual_checkpoints=["Requires explicit confirmation before any live-submit path.", "Default beginner usage should stay in dry-run or report-only mode."],
                requires_confirmation=True,
                mutates_state=True,
                connects_remote=True,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="classic_multifactor.daily_runner",
                path="scripts/classic_multifactor/run_daily_rebalance.py",
                stage="simulation",
                description="Daily rebalance mainline runner with shared execution guards and dry-run by default.",
                inputs=["classic config json", "rebalance time", "max bars", "live-submit switches"],
                outputs=["state/runs/classic_multifactor_daily_report.json", "state/runs/events.jsonl", "state/runs/orders/*.json"],
                manual_checkpoints=["Review target_positions and daily turnover assumptions before execution."],
                requires_confirmation=True,
                mutates_state=True,
                connects_remote=True,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="classic_multifactor.vnpy_backtest",
                path="scripts/classic_multifactor/run_vnpy_cta_backtest.py",
                stage="backtest",
                description="Official vn.py CTA backtest entry for classic multifactor strategy.",
                inputs=["symbol", "start/end", "capital", "interval", "fees and slippage", "strategy parameters"],
                outputs=["state/runs/classic_multifactor/vnpy_cta_backtest_report.json"],
                manual_checkpoints=["Check sample period, slippage, fee assumptions and stability metrics before upgrade."],
                requires_confirmation=True,
                mutates_state=True,
                connects_remote=False,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="llm.stock_analysis",
                path="scripts/llm/analyze_stock_with_llm.py",
                stage="research",
                description="Single-stock LLM-assisted analysis helper using market context and optional technical overlays.",
                inputs=["symbol", "timeframe", "technical/capital/derivatives toggles"],
                outputs=["stdout analysis text"],
                manual_checkpoints=["Treat output as research aid only; do not bypass rule-based screening or risk review."],
                requires_confirmation=False,
                mutates_state=False,
                connects_remote=True,
                safe_by_default=True,
            ),
            CapabilityDefinition(
                capability_id="llm.anomaly_detector",
                path="scripts/llm/anomaly_detector.py",
                stage="research",
                description="Aggregates technical, capital-flow and derivatives anomaly checks for a symbol.",
                inputs=["symbol", "anomaly type flags"],
                outputs=["stdout anomaly snapshot"],
                manual_checkpoints=["Use as explanatory context; not as a direct execution signal."],
                requires_confirmation=False,
                mutates_state=False,
                connects_remote=True,
                safe_by_default=True,
            ),
            CapabilityDefinition(
                capability_id="llm.config",
                path="scripts/llm/config.py",
                stage="research",
                description="LLM analysis configuration and prompt-template support module.",
                inputs=["market", "symbol", "timeframe", "technical/capital toggles"],
                outputs=["in-memory prompt and config objects"],
                manual_checkpoints=["Human should confirm market and timeframe assumptions."],
                requires_confirmation=False,
                mutates_state=False,
                connects_remote=False,
                safe_by_default=True,
            ),
            CapabilityDefinition(
                capability_id="healthcheck.report",
                path="scripts/run_healthcheck.py",
                stage="research",
                description="Read-only environment and account health check.",
                inputs=["repository root"],
                outputs=["state/runs/healthcheck.json"],
                manual_checkpoints=["Inspect alerts before enabling any execution stage."],
                requires_confirmation=False,
                mutates_state=False,
                connects_remote=True,
                safe_by_default=True,
            ),
            CapabilityDefinition(
                capability_id="portfolio.brief",
                path="scripts/run_portfolio_brief.py",
                stage="research",
                description="Portfolio summary and concentration/risk brief builder.",
                inputs=["hk/us close reports", "healthcheck report"],
                outputs=["state/runs/portfolio_brief.json"],
                manual_checkpoints=["Verify portfolio concentration and alerts before moving to simulation."],
                requires_confirmation=False,
                mutates_state=False,
                connects_remote=False,
                safe_by_default=True,
            ),
            CapabilityDefinition(
                capability_id="sim.us_legacy_task",
                path="scripts/run_us_sim_task.py",
                stage="simulation",
                description="US SIM trading task entry using legacy pipeline or forwarding into vnpy mainline.",
                inputs=["candidate inputs", "market quotes", "optional classic config"],
                outputs=["state/runs/us_sim_task_report.json", "state/runs/us_sim_account.json"],
                manual_checkpoints=["Review selected candidates and budget assumptions before running.", "Prefer dry-run or report review for beginner workflow."],
                requires_confirmation=True,
                mutates_state=True,
                connects_remote=True,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="sim.us_futu_session",
                path="scripts/run_us_futu_sim_session.py",
                stage="simulation",
                description="US Futu SIM session loop with budget/loss constraints and state writes.",
                inputs=["candidate inputs", "quote retries", "max budget", "max loss"],
                outputs=["state/runs/us_futu_sim_session_report.json", "state/runs/us_futu_sim_session_state.json", "state/runs/orders/*.json"],
                manual_checkpoints=["Requires explicit user confirmation before session execution.", "Must stay in Futu SIMULATE environment only."],
                requires_confirmation=True,
                mutates_state=True,
                connects_remote=True,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="live.us_task",
                path="scripts/run_us_live_task.py",
                stage="live",
                description="US live-task gate; dry-run by default and guarded by explicit live switches.",
                inputs=["candidate inputs", "budget settings", "live-submit flag", "environment switches"],
                outputs=["state/runs/us_live_task_report.json", "state/runs/orders/*.json"],
                manual_checkpoints=["Real execution must be explicitly approved.", "Confirm VNPY_LIVE_CONFIG/VNPY_LIVE_SUBMIT/VNPY_LIVE_APPROVED before any non-dry-run path."],
                requires_confirmation=True,
                mutates_state=True,
                connects_remote=True,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="workflow.quant_entry",
                path="scripts/quant_workflow/run_quant_workflow.py",
                stage="orchestration",
                description="Planned unified beginner quant workflow entry for research, planning, validation and optional simulation orchestration.",
                inputs=["workflow preset", "market", "symbol list", "risk profile", "execution mode"],
                outputs=["state/runs/quant_workflow/*.json"],
                manual_checkpoints=["Execution-related stages require explicit confirmation.", "Beginner workflow should default to research/planning mode."],
                requires_confirmation=True,
                mutates_state=True,
                connects_remote=True,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="gap.hk_sim_task",
                path="scripts/run_hk_sim_task.py",
                stage="simulation",
                description="Mentioned by project conventions but currently missing in repository; treat as a capability gap.",
                inputs=["N/A"],
                outputs=["N/A"],
                manual_checkpoints=["Use US-only examples or a manual HK simulation flow until this entry exists."],
                requires_confirmation=True,
                mutates_state=False,
                connects_remote=False,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="gap.hk_live_task",
                path="scripts/run_hk_live_task.py",
                stage="live",
                description="Mentioned by project conventions but currently missing in repository; treat as a capability gap.",
                inputs=["N/A"],
                outputs=["N/A"],
                manual_checkpoints=["Do not claim HK live support in automated workflow until the entry exists."],
                requires_confirmation=True,
                mutates_state=False,
                connects_remote=False,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="gap.hk_futu_session",
                path="scripts/run_hk_futu_sim_session.py",
                stage="simulation",
                description="Documented in integration guide but currently missing in repository; treat as capability gap.",
                inputs=["N/A"],
                outputs=["N/A"],
                manual_checkpoints=["Use alternative manual HK SIM flow until this entry exists."],
                requires_confirmation=True,
                mutates_state=False,
                connects_remote=False,
                safe_by_default=False,
            ),
        ]


class CapabilityStageResolver:
    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self.registry = registry or CapabilityRegistry()

    def available_stages(self, *, beginner_mode: bool = False) -> dict[str, list[CapabilityDefinition]]:
        stages = self.registry.stage_boundary_map().keys()
        return {
            stage: self.registry.select_for_stage(stage, include_unsafe=not beginner_mode)
            for stage in stages
            if self.registry.select_for_stage(stage, include_unsafe=not beginner_mode)
        }

    def recommend_stage_order(self) -> list[str]:
        return ["research", "backtest", "simulation", "live"]

    def gaps_by_stage(self) -> dict[str, list[CapabilityGap]]:
        grouped: dict[str, list[CapabilityGap]] = {"research": [], "backtest": [], "simulation": [], "live": [], "orchestration": []}
        for gap in self.registry.capability_gaps():
            item = self.registry.get(gap.capability_id)
            stage = item.stage if item else "research"
            grouped.setdefault(stage, []).append(gap)
        return grouped


def collect_capabilities(*registries: Iterable[CapabilityDefinition]) -> list[CapabilityDefinition]:
    items: list[CapabilityDefinition] = []
    for registry in registries:
        items.extend(list(registry))
    return items


def flatten_capability_outputs(items: Sequence[CapabilityDefinition]) -> list[str]:
    outputs: list[str] = []
    for item in items:
        outputs.extend(item.outputs)
    return outputs
