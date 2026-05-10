from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import (
    CandidateObservation,
    CapabilityDefinition,
    CapabilityGap,
    ConflictNote,
    EvidenceItem,
    ExplanationSection,
    PlanPhase,
    PlanTask,
    ReadinessChecklist,
    ResearchFinding,
    RiskBudget,
    UnifiedOutputSchema,
    WorkflowRunResult,
)


@dataclass
class SchemaValidator:
    """统一输出schema验证器"""

    @staticmethod
    def validate_knowledge_sections(sections: List[ExplanationSection]) -> List[str]:
        """验证知识说明部分"""
        errors = []
        for i, section in enumerate(sections):
            if not section.title:
                errors.append(f"知识说明{i+1}: 标题不能为空")
            if not section.summary:
                errors.append(f"知识说明{i+1}: 摘要不能为空")
        return errors

    @staticmethod
    def validate_research_conclusions(findings: List[ResearchFinding]) -> List[str]:
        """验证研究结论部分"""
        errors = []
        for i, finding in enumerate(findings):
            if not finding.topic:
                errors.append(f"研究结论{i+1}: 主题不能为空")
            if not finding.conclusion:
                errors.append(f"研究结论{i+1}: 结论不能为空")
            if finding.confidence < 0 or finding.confidence > 1:
                errors.append(f"研究结论{i+1}: 置信度必须在0-1之间")
        return errors

    @staticmethod
    def validate_execution_suggestions(suggestions: List[str]) -> List[str]:
        """验证执行建议部分"""
        errors = []
        for i, suggestion in enumerate(suggestions):
            if not suggestion.strip():
                errors.append(f"执行建议{i+1}: 内容不能为空")
        return errors

    @staticmethod
    def validate_risk_prompts(prompts: List[str]) -> List[str]:
        """验证风险提示部分"""
        errors = []
        for i, prompt in enumerate(prompts):
            if not prompt.strip():
                errors.append(f"风险提示{i+1}: 内容不能为空")
        return errors

    def validate_schema(self, schema: UnifiedOutputSchema) -> Dict[str, Any]:
        """完整验证schema"""
        errors = []

        errors.extend(self.validate_knowledge_sections(schema.knowledge_sections))
        errors.extend(self.validate_research_conclusions(schema.research_conclusions))
        errors.extend(self.validate_execution_suggestions(schema.execution_suggestions))
        errors.extend(self.validate_risk_prompts(schema.risk_prompts))

        if not schema.timestamp:
            errors.append("时间戳不能为空")
        if not schema.version:
            errors.append("版本号不能为空")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": [] if errors else ["Schema验证通过"]
        }


@dataclass
class OrchestrationInterface:
    """编排接口"""

    def build_beginner_quant_schema(
        self,
        knowledge_sections: List[ExplanationSection],
        research_findings: List[ResearchFinding],
        execution_suggestions: List[str],
        risk_prompts: List[str],
        evidence_items: List[EvidenceItem],
        conflicts: List[ConflictNote],
        assumptions: List[str],
        invalidation_conditions: List[str],
        confidence_level: str = "medium"
    ) -> UnifiedOutputSchema:
        """构建新手量化schema"""
        return UnifiedOutputSchema(
            knowledge_sections=knowledge_sections,
            research_conclusions=research_findings,
            execution_suggestions=execution_suggestions,
            risk_prompts=risk_prompts,
            timestamp=datetime.now(timezone.utc).isoformat(),
            version="v1.0",
            assumptions=assumptions,
            invalidation_conditions=invalidation_conditions,
            sources=evidence_items,
            confidence_level=confidence_level
        )

    def build_research_evidence_bundle(
        self,
        evidence_items: List[EvidenceItem],
        source_time: str,
        evidence_strength: str,
        conflicting_viewpoints: List[str],
        low_confidence_items: List[str],
        pending_verification_items: List[str],
        confidence_score: float
    ) -> Dict[str, Any]:
        """构建研究证据包"""
        return {
            "evidence_items": evidence_items,
            "standardization": {
                "source_time": source_time,
                "evidence_strength": evidence_strength,
                "conflicting_viewpoints": conflicting_viewpoints,
                "low_confidence_items": low_confidence_items,
                "pending_verification_items": pending_verification_items,
                "confidence_score": confidence_score
            },
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": "v1.0"
            }
        }

    def build_capability_registry_entry(
        self,
        capability_id: str,
        path: str,
        stage: str,
        description: str,
        inputs: List[str],
        outputs: List[str],
        manual_checkpoints: List[str],
        requires_confirmation: bool = False,
        mutates_state: bool = False,
        connects_remote: bool = False
    ) -> CapabilityDefinition:
        """构建能力注册表条目"""
        return CapabilityDefinition(
            capability_id=capability_id,
            path=path,
            stage=stage,
            description=description,
            inputs=inputs,
            outputs=outputs,
            manual_checkpoints=manual_checkpoints,
            requires_confirmation=requires_confirmation,
            mutates_state=mutates_state,
            connects_remote=connects_remote,
            safe_by_default=True
        )

    def build_planning_artifact(
        self,
        artifact_type: str,
        title: str,
        knowledge_sections: List[ExplanationSection],
        research_findings: List[ResearchFinding],
        execution_suggestions: List[str],
        risk_prompts: List[str],
        evidence_items: List[EvidenceItem],
        conflicts: List[ConflictNote],
        candidate_observations: List[CandidateObservation],
        capability_map: List[CapabilityDefinition],
        capability_gaps: List[CapabilityGap],
        plan_phases: List[PlanPhase],
        risk_budget: Optional[RiskBudget] = None,
        readiness: Optional[ReadinessChecklist] = None,
        workflow: Optional[WorkflowRunResult] = None
    ) -> Dict[str, Any]:
        """构建规划产物"""
        return {
            "artifact_type": artifact_type,
            "title": title,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": "v1.0",
            "knowledge_sections": knowledge_sections,
            "research_findings": research_findings,
            "research_conclusions": [f.conclusion for f in research_findings],
            "execution_suggestions": execution_suggestions,
            "risk_prompts": risk_prompts,
            "evidence_items": evidence_items,
            "conflicts": conflicts,
            "candidate_observations": candidate_observations,
            "capability_map": capability_map,
            "capability_gaps": capability_gaps,
            "plan_phases": plan_phases,
            "risk_budget": risk_budget,
            "readiness": readiness,
            "workflow": workflow,
            "meta": {
                "schema": "unified_output_v1",
                "content_categories": ["knowledge", "research", "execution", "risk"]
            }
        }


class SchemaRenderer:
    """Schema渲染器"""

    @staticmethod
    def render_markdown(schema: UnifiedOutputSchema) -> str:
        """渲染为Markdown格式"""
        sections = []

        # 知识说明部分
        sections.append("# 知识说明")
        for section in schema.knowledge_sections:
            sections.append(f"## {section.title}")
            sections.append(section.summary)
            if section.beginner_actions:
                sections.append("### 新手行动建议")
                for action in section.beginner_actions:
                    sections.append(f"- {action}")

        # 研究结论部分
        sections.append("# 研究结论")
        for finding in schema.research_conclusions:
            sections.append(f"## {finding.topic}")
            sections.append(finding.conclusion)
            sections.append(f"置信度: {finding.confidence:.1%}")

        # 执行建议部分
        sections.append("# 执行建议")
        for suggestion in schema.execution_suggestions:
            sections.append(f"- {suggestion}")

        # 风险提示部分
        sections.append("# 风险提示")
        for prompt in schema.risk_prompts:
            sections.append(f"- {prompt}")

        return "\n\n".join(sections)

    @staticmethod
    def render_json(schema: UnifiedOutputSchema) -> Dict[str, Any]:
        """渲染为JSON格式"""
        return {
            "timestamp": schema.timestamp,
            "version": schema.version,
            "knowledge_sections": [asdict(section) for section in schema.knowledge_sections],
            "research_conclusions": [asdict(finding) for finding in schema.research_conclusions],
            "execution_suggestions": schema.execution_suggestions,
            "risk_prompts": schema.risk_prompts,
            "assumptions": schema.assumptions,
            "invalidation_conditions": schema.invalidation_conditions,
            "sources": [asdict(source) for source in schema.sources],
            "confidence_level": schema.confidence_level
        }