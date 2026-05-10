from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Sequence, Any

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
                capability_id="sim.hk_task",
                path="scripts/run_hk_sim_task.py",
                stage="simulation",
                description="HK SIM trading task entry using legacy pipeline or forwarding into vnpy mainline.",
                inputs=["candidate inputs", "market quotes", "optional classic config"],
                outputs=["state/runs/hk_sim_task_report.json", "state/runs/hk_sim_account.json"],
                manual_checkpoints=["Review HK lot-size assumptions and selected candidates before running.", "Prefer dry-run or report review for beginner workflow."],
                requires_confirmation=True,
                mutates_state=True,
                connects_remote=True,
                safe_by_default=False,
            ),
            CapabilityDefinition(
                capability_id="sim.hk_futu_session",
                path="scripts/run_hk_futu_sim_session.py",
                stage="simulation",
                description="HK Futu SIM session wrapper with Hong Kong session defaults and state/report writes.",
                inputs=["classic config", "session window", "HK report defaults"],
                outputs=["state/runs/hk_futu_sim_session_report.json", "state/runs/events.jsonl", "state/runs/orders/*.json"],
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
                capability_id="live.hk_task",
                path="scripts/run_hk_live_task.py",
                stage="live",
                description="HK live-task gate; dry-run by default and guarded by explicit live switches.",
                inputs=["candidate inputs", "budget settings", "live-submit flag", "environment switches"],
                outputs=["state/runs/hk_live_task_report.json", "state/runs/orders/*.json"],
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
        ]


class EnhancedCapabilityRegistry(CapabilityRegistry):
    """增强能力注册表，支持更详细的阶段边界映射和本地脚本能力"""

    def __init__(self) -> None:
        super().__init__()
        self._stage_boundaries = self._build_stage_boundaries()

    def _build_stage_boundaries(self) -> dict[str, dict[str, Any]]:
        """构建详细的阶段边界映射"""
        return {
            "research": {
                "description": "研究阶段 - 知识学习、策略研究、数据验证",
                "allowed_capabilities": ["classic_multifactor.llm_research", "llm.stock_analysis", "llm.anomaly_detector", "healthcheck.report", "portfolio.brief"],
                "safe_by_default": True,
                "confirmation_required": False,
                "output_types": ["research_report", "knowledge_document", "candidate_list"],
                "next_stage": "backtest",
                "upgrade_conditions": ["完成基础知识学习", "验证数据质量", "建立候选观察名单"]
            },
            "backtest": {
                "description": "回测阶段 - 策略验证、参数调优、过拟合检查",
                "allowed_capabilities": ["classic_multifactor.vnpy_backtest"],
                "safe_by_default": True,
                "confirmation_required": True,
                "output_types": ["backtest_report", "performance_metrics", "stability_analysis"],
                "next_stage": "simulation",
                "upgrade_conditions": ["通过稳健性测试", "验证交易成本", "确认样本外表现"]
            },
            "simulation": {
                "description": "模拟阶段 - 实盘模拟、执行验证、风控测试",
                "allowed_capabilities": ["classic_multifactor.intraday_runner", "classic_multifactor.daily_runner", "sim.us_legacy_task", "sim.us_futu_session", "sim.hk_task", "sim.hk_futu_session"],
                "safe_by_default": False,
                "confirmation_required": True,
                "output_types": ["simulation_report", "execution_log", "risk_monitor"],
                "next_stage": "live",
                "upgrade_conditions": ["模拟运行稳定", "风控系统验证", "执行质量达标"]
            },
            "live": {
                "description": "实盘阶段 - 真实交易、资金管理、持续监控",
                "allowed_capabilities": ["live.us_task", "live.hk_task"],
                "safe_by_default": False,
                "confirmation_required": True,
                "output_types": ["live_trading_report", "account_statement", "risk_dashboard"],
                "next_stage": None,
                "upgrade_conditions": []
            },
            "orchestration": {
                "description": "编排阶段 - 工作流管理、多阶段协调、结果汇总",
                "allowed_capabilities": ["workflow.quant_entry"],
                "safe_by_default": False,
                "confirmation_required": True,
                "output_types": ["workflow_summary", "stage_transition_log", "artifact_collection"],
                "next_stage": None,
                "upgrade_conditions": []
            }
        }

    def get_stage_boundary_info(self, stage: str) -> dict[str, Any]:
        """获取阶段边界详细信息"""
        return self._stage_boundaries.get(stage, {})

    def get_capabilities_by_stage_with_details(self, stage: str) -> list[dict[str, Any]]:
        """获取指定阶段的详细能力信息"""
        capabilities = self.by_stage(stage)
        stage_info = self.get_stage_boundary_info(stage)

        result = []
        for capability in capabilities:
            capability_info = asdict(capability)
            capability_info.update({
                "stage_description": stage_info.get("description", ""),
                "allowed_in_stage": capability.capability_id in stage_info.get("allowed_capabilities", []),
                "safe_by_default": stage_info.get("safe_by_default", True),
                "confirmation_required": stage_info.get("confirmation_required", False),
                "output_types": stage_info.get("output_types", []),
                "next_stage": stage_info.get("next_stage"),
                "upgrade_conditions": stage_info.get("upgrade_conditions", [])
            })
            result.append(capability_info)

        return result

    def validate_stage_transition(self, from_stage: str, to_stage: str) -> dict[str, Any]:
        """验证阶段转换是否允许"""
        from_info = self.get_stage_boundary_info(from_stage)
        to_info = self.get_stage_boundary_info(to_stage)

        if not from_info or not to_info:
            return {"valid": False, "reason": "无效的阶段名称"}

        if from_info.get("next_stage") != to_stage:
            return {"valid": False, "reason": f"从{from_stage}到{to_stage}的阶段转换不被允许"}

        return {"valid": True, "reason": "阶段转换验证通过"}

    def get_stage_readiness_checklist(self, stage: str) -> list[str]:
        """获取阶段准备度检查清单"""
        stage_info = self.get_stage_boundary_info(stage)
        upgrade_conditions = stage_info.get("upgrade_conditions", [])

        checklist = []
        for condition in upgrade_conditions:
            checklist.append(f"✓ {condition}")

        if stage == "backtest":
            checklist.extend([
                "✓ 数据质量验证完成",
                "✓ 交易成本假设明确",
                "✓ 样本外测试计划制定"
            ])
        elif stage == "simulation":
            checklist.extend([
                "✓ 回测结果稳健性确认",
                "✓ 风控规则定义完成",
                "✓ 模拟环境配置就绪"
            ])
        elif stage == "live":
            checklist.extend([
                "✓ 模拟运行稳定通过",
                "✓ 资金管理计划制定",
                "✓ 应急预案准备完成"
            ])

        return checklist


class LocalScriptCapabilityMapper:
    """本地脚本能力映射器"""

    def __init__(self, registry: EnhancedCapabilityRegistry):
        self.registry = registry

    def map_script_to_capability(self, script_path: str) -> CapabilityDefinition | None:
        """将脚本路径映射到能力定义"""
        for capability in self.registry.list_all():
            if capability.path == script_path:
                return capability
        return None

    def get_capabilities_by_directory(self, directory: str) -> list[CapabilityDefinition]:
        """获取指定目录下的所有能力"""
        return [cap for cap in self.registry.list_all() if cap.path.startswith(directory)]

    def get_classic_multifactor_capabilities(self) -> list[CapabilityDefinition]:
        """获取classic_multifactor目录下的能力"""
        return self.get_capabilities_by_directory("scripts/classic_multifactor/")

    def get_llm_capabilities(self) -> list[CapabilityDefinition]:
        """获取LLM相关能力"""
        return self.get_capabilities_by_directory("scripts/llm/")

    def get_automation_entry_capabilities(self) -> list[CapabilityDefinition]:
        """获取自动化入口能力"""
        return [cap for cap in self.registry.list_all() if cap.stage == "orchestration"]

    def get_manual_checkpoints_by_stage(self, stage: str) -> list[str]:
        """获取指定阶段的人工确认点"""
        capabilities = self.registry.by_stage(stage)
        checkpoints = []
        for capability in capabilities:
            checkpoints.extend(capability.manual_checkpoints)
        return list(set(checkpoints))


class StageBoundaryManager:
    """阶段边界管理器"""

    def __init__(self, registry: EnhancedCapabilityRegistry):
        self.registry = registry

    def get_stage_progression_path(self) -> list[dict[str, Any]]:
        """获取阶段演进路径"""
        stages = ["research", "backtest", "simulation", "live"]
        path = []

        for i, stage in enumerate(stages):
            stage_info = self.registry.get_stage_boundary_info(stage)
            next_stage = stages[i + 1] if i + 1 < len(stages) else None

            path.append({
                "stage": stage,
                "description": stage_info.get("description", ""),
                "safe_by_default": stage_info.get("safe_by_default", True),
                "confirmation_required": stage_info.get("confirmation_required", False),
                "next_stage": next_stage,
                "upgrade_conditions": stage_info.get("upgrade_conditions", []),
                "capabilities": len(self.registry.by_stage(stage))
            })

        return path

    def can_upgrade_to_stage(self, current_stage: str, target_stage: str) -> dict[str, Any]:
        """检查是否可以升级到目标阶段"""
        validation = self.registry.validate_stage_transition(current_stage, target_stage)

        if not validation["valid"]:
            return validation

        target_capabilities = self.registry.by_stage(target_stage)
        if not target_capabilities:
            return {"valid": False, "reason": f"目标阶段{target_stage}没有可用的能力"}

        return {"valid": True, "reason": "可以升级到目标阶段"}

    def get_stage_dependencies(self, stage: str) -> list[str]:
        """获取阶段依赖关系"""
        dependencies = []

        if stage == "backtest":
            dependencies = ["research"]
        elif stage == "simulation":
            dependencies = ["research", "backtest"]
        elif stage == "live":
            dependencies = ["research", "backtest", "simulation"]

        return dependencies


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
