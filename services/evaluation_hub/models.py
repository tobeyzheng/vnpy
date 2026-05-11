from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class EvaluationSignal:
    symbol: str
    market: str
    source: str
    dimension: str
    score: float
    confidence: float
    summary: str
    risks: List[str] = field(default_factory=list)
    action_bias: str = "neutral"
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationBundle:
    symbol: str
    market: str
    signals: List[EvaluationSignal] = field(default_factory=list)


@dataclass
class EvidenceItem:
    title: str
    source_url: str = ""
    published_at: str = ""
    source_type: str = "public_web"
    evidence_level: str = "low"
    note: str = ""


@dataclass
class ResearchFinding:
    topic: str
    conclusion: str
    evidence_level: str = "low"
    source_kind: str = "public_research"
    confidence: float = 0.5
    verification_status: str = "verified"
    invalidation_conditions: List[str] = field(default_factory=list)
    references: List[EvidenceItem] = field(default_factory=list)


@dataclass
class ConflictNote:
    topic: str
    viewpoints: List[str] = field(default_factory=list)
    applicability: str = ""
    conservative_takeaway: str = ""


@dataclass
class TerminologyItem:
    term: str
    definition: str
    example: str = ""
    common_misunderstanding: str = ""


@dataclass
class ExplanationSection:
    title: str
    summary: str
    beginner_actions: List[str] = field(default_factory=list)
    avoid_for_now: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    terms: List[TerminologyItem] = field(default_factory=list)
    references: List[EvidenceItem] = field(default_factory=list)


@dataclass
class RenderedDocument:
    """渲染文档"""
    title: str
    format: str
    body: str
    section_count: int
    version: str
    generated_at: str


@dataclass
class PlanAssumption:
    name: str
    value: str
    reason: str = ""


@dataclass
class PlanTask:
    period: str
    title: str
    actions: List[str] = field(default_factory=list)
    review_points: List[str] = field(default_factory=list)
    pause_conditions: List[str] = field(default_factory=list)


@dataclass
class PlanPhase:
    """计划阶段模型"""
    name: str
    goal: str
    duration_hint: str
    tasks: List[PlanTask] = field(default_factory=list)
    readiness_checks: List[ReadinessCheckItem] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    upgrade_conditions: List[str] = field(default_factory=list)


@dataclass
class CandidateObservation:
    """候选观察项"""
    symbol: str
    market: str
    selected_as: str
    score: float
    reasons: List[str] = field(default_factory=list)
    primary_risks: List[str] = field(default_factory=list)
    validation_points: List[str] = field(default_factory=list)
    bucket: str = ""
    trading_level: str = ""
    hard_risk_flags: List[str] = field(default_factory=list)
    soft_risk_flags: List[str] = field(default_factory=list)
    research_confidence: float | None = None
    liquidity_score: float | None = None
    risk_penalty: float | None = None
    raw_score: float | None = None
    rank_score: float | None = None
    backtest_ready: bool = False
    manual_review_required: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    def effective_bucket(self) -> str:
        return self.bucket or self.selected_as


@dataclass
class RiskBudget:
    """风险预算模型"""
    single_position_limit_pct: float
    total_exposure_limit_pct: float
    max_positions: int
    sector_limit_pct: float
    daily_new_risk_budget_pct: float
    stop_conditions: List[str] = field(default_factory=list)
    pause_conditions: List[str] = field(default_factory=list)
    review_conditions: List[str] = field(default_factory=list)


@dataclass
class ReadinessCheckItem:
    """准备度检查项"""
    name: str
    passed: bool
    severity: str = "medium"
    details: str = ""
    remediation: str = ""


@dataclass
class ReadinessChecklist:
    """准备度检查清单"""
    stage: str
    items: List[ReadinessCheckItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        high_risk_failures = [item for item in self.items if not item.passed and item.severity in {"high", "critical"}]
        return not high_risk_failures

    def failed_items(self) -> List[ReadinessCheckItem]:
        return [item for item in self.items if not item.passed]


@dataclass
class CapabilityDefinition:
    """能力定义"""
    capability_id: str
    path: str
    stage: str
    description: str
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    manual_checkpoints: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    mutates_state: bool = False
    connects_remote: bool = False
    safe_by_default: bool = True


@dataclass
class CapabilityGap:
    """能力缺口"""
    capability_id: str
    expected_path: str
    reason: str
    manual_alternative: str = ""
    suggested_next_step: str = ""


@dataclass
class WorkflowStepResult:
    """工作流步骤结果"""
    step: str
    status: str
    message: str
    outputs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRunResult:
    """工作流运行结果"""
    workflow_name: str
    mode: str
    started_at: str
    status: str
    steps: List[WorkflowStepResult] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    assumptions: List[PlanAssumption] = field(default_factory=list)


@dataclass
class PlanningArtifact:
    artifact_type: str
    title: str
    generated_at: str
    version: str
    assumptions: List[PlanAssumption] = field(default_factory=list)
    knowledge_sections: List[ExplanationSection] = field(default_factory=list)
    research_findings: List[ResearchFinding] = field(default_factory=list)
    research_conclusions: List[str] = field(default_factory=list)
    execution_suggestions: List[str] = field(default_factory=list)
    risk_prompts: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)
    evidence_items: List[EvidenceItem] = field(default_factory=list)
    conflicts: List[ConflictNote] = field(default_factory=list)
    candidate_observations: List[CandidateObservation] = field(default_factory=list)
    capability_map: List[CapabilityDefinition] = field(default_factory=list)
    capability_gaps: List[CapabilityGap] = field(default_factory=list)
    plan_phases: List[PlanPhase] = field(default_factory=list)
    risk_budget: RiskBudget | None = None
    readiness: ReadinessChecklist | None = None
    workflow: WorkflowRunResult | None = None
    rendered_documents: List[RenderedDocument] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UnifiedOutputSchema:
    """统一输出schema，强制区分知识说明/研究结论/执行建议/风险提示"""
    knowledge_sections: List[ExplanationSection] = field(default_factory=list)
    research_conclusions: List[ResearchFinding] = field(default_factory=list)
    execution_suggestions: List[str] = field(default_factory=list)
    risk_prompts: List[str] = field(default_factory=list)
    timestamp: str = ""
    version: str = "v1"
    assumptions: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)
    sources: List[EvidenceItem] = field(default_factory=list)
    confidence_level: str = "medium"

    def validate(self) -> bool:
        """验证schema完整性"""
        required_fields = [
            self.knowledge_sections,
            self.research_conclusions,
            self.execution_suggestions,
            self.risk_prompts,
        ]
        return all(field for field in required_fields)


@dataclass
class EvidenceStandardization:
    """证据标准化记录"""
    source_time: str
    evidence_strength: str
    conflicting_viewpoints: List[str] = field(default_factory=list)
    low_confidence_items: List[str] = field(default_factory=list)
    pending_verification_items: List[str] = field(default_factory=list)
    confidence_score: float = 0.0

    def get_evidence_level(self) -> str:
        if self.confidence_score >= 0.8:
            return "high"
        if self.confidence_score >= 0.6:
            return "medium"
        return "low"


@dataclass
class ResearchEvidenceBundle:
    """研究证据包"""
    evidence_items: List[EvidenceItem]
    standardization: EvidenceStandardization
    conflicts: List[ConflictNote]
    meta: Dict[str, Any] = field(default_factory=dict)

    def add_evidence(self, evidence: EvidenceItem) -> None:
        self.evidence_items.append(evidence)

    def add_conflict(self, conflict: ConflictNote) -> None:
        self.conflicts.append(conflict)
