from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from vnpy_llm.base import beijing_now_isoformat

from .models import (
    ConflictNote,
    EvidenceItem,
    ResearchFinding,
    UnifiedOutputSchema,
)


@dataclass
class EvidenceStandardizationResult:
    """证据标准化结果"""
    source_time: str
    evidence_strength: str
    confidence_score: float
    conflicting_viewpoints: List[str] = field(default_factory=list)
    low_confidence_items: List[str] = field(default_factory=list)
    pending_verification_items: List[str] = field(default_factory=list)
    standardized_evidence: List[EvidenceItem] = field(default_factory=list)
    research_findings: List[ResearchFinding] = field(default_factory=list)
    conflicts: List[ConflictNote] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def get_overall_confidence_level(self) -> str:
        """获取整体置信度级别"""
        if self.confidence_score >= 0.8:
            return "high"
        elif self.confidence_score >= 0.6:
            return "medium"
        return "low"


class EvidenceStandardizer:
    """证据标准化器"""

    def __init__(self):
        self._public_sources = self._init_public_sources()

    def standardize_llm_research_result(
        self,
        llm_result: Dict[str, Any],
        source_time: Optional[str] = None,
        schema_preset: str = "beginner_quant"
    ) -> EvidenceStandardizationResult:
        """标准化LLM研究结果"""
        source_time = source_time or beijing_now_isoformat()

        # 提取证据项
        evidence_items = self._extract_evidence_items(llm_result)

        # 提取研究结论
        research_findings = self._extract_research_findings(llm_result, evidence_items)

        # 检测冲突观点
        conflicts = self._detect_conflicts(llm_result)

        # 识别低置信度项
        low_confidence_items = self._identify_low_confidence_items(llm_result)

        # 识别待验证项
        pending_verification_items = self._identify_pending_verification_items(llm_result)

        # 计算置信度分数
        confidence_score = self._calculate_confidence_score(
            evidence_items, research_findings, conflicts
        )

        # 确定证据强度
        evidence_strength = self._determine_evidence_strength(confidence_score)

        return EvidenceStandardizationResult(
            source_time=source_time,
            evidence_strength=evidence_strength,
            confidence_score=confidence_score,
            conflicting_viewpoints=self._extract_conflicting_viewpoints(conflicts),
            low_confidence_items=low_confidence_items,
            pending_verification_items=pending_verification_items,
            standardized_evidence=evidence_items,
            research_findings=research_findings,
            conflicts=conflicts,
            meta={
                "schema_preset": schema_preset,
                "source_type": "llm_research",
                "standardization_version": "v1.0"
            }
        )

    def _extract_evidence_items(self, result: Dict[str, Any]) -> List[EvidenceItem]:
        """提取证据项"""
        evidence_items = []

        # 从references字段提取
        references = result.get("references", [])
        if isinstance(references, list):
            for ref in references:
                if isinstance(ref, dict):
                    evidence_items.append(self._create_evidence_item(ref))

        return evidence_items

    def _create_evidence_item(self, ref: Dict[str, Any]) -> EvidenceItem:
        """创建证据项"""
        title = str(ref.get("title") or ref.get("name") or "unknown_reference")
        url = str(ref.get("url") or ref.get("source_url") or ref.get("link") or "")
        published_at = str(ref.get("published_at") or ref.get("date") or ref.get("time") or "")

        # 确定证据级别
        evidence_level = self._determine_evidence_level(ref)

        # 确定来源类型
        source_type = self._determine_source_type(url)

        return EvidenceItem(
            title=title,
            source_url=url,
            published_at=published_at,
            source_type=source_type,
            evidence_level=evidence_level,
            note=str(ref.get("note") or ref.get("summary") or "")
        )

    def _extract_research_findings(
        self,
        result: Dict[str, Any],
        evidence_items: List[EvidenceItem]
    ) -> List[ResearchFinding]:
        """提取研究结论"""
        findings = []

        # 从core_concepts提取
        core_concepts = result.get("core_concepts", [])
        if isinstance(core_concepts, list):
            for concept in core_concepts:
                if isinstance(concept, str):
                    findings.append(ResearchFinding(
                        topic="core_concept",
                        conclusion=concept,
                        evidence_level="high",
                        source_kind="public_knowledge",
                        confidence=0.8,
                        verification_status="verified",
                        references=evidence_items
                    ))

        # 从beginner_safe_practices提取
        practices = result.get("beginner_safe_practices", [])
        if isinstance(practices, list):
            for practice in practices:
                if isinstance(practice, str):
                    findings.append(ResearchFinding(
                        topic="beginner_practice",
                        conclusion=practice,
                        evidence_level="medium",
                        source_kind="industry_best_practice",
                        confidence=0.7,
                        verification_status="verified",
                        references=evidence_items
                    ))

        return findings

    def _detect_conflicts(self, result: Dict[str, Any]) -> List[ConflictNote]:
        """检测冲突观点"""
        conflicts = []

        # 从conflicting_viewpoints提取
        viewpoints = result.get("conflicting_viewpoints", [])
        if isinstance(viewpoints, list):
            if viewpoints:
                conflicts.append(ConflictNote(
                    topic="conflicting_viewpoints",
                    viewpoints=viewpoints,
                    applicability="general",
                    conservative_takeaway="Prefer the more conservative interpretation until verified."
                ))

        return conflicts

    def _identify_low_confidence_items(self, result: Dict[str, Any]) -> List[str]:
        """识别低置信度项"""
        low_confidence_items = []

        # 从low_confidence_items提取
        items = result.get("low_confidence_items", [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    low_confidence_items.append(item)

        return low_confidence_items

    def _identify_pending_verification_items(self, result: Dict[str, Any]) -> List[str]:
        """识别待验证项"""
        pending_items = []

        # 检查是否有缺少证据的结论
        if not result.get("references"):
            pending_items.append("Research lacks verifiable public references")

        return pending_items

    def _calculate_confidence_score(
        self,
        evidence_items: List[EvidenceItem],
        research_findings: List[ResearchFinding],
        conflicts: List[ConflictNote]
    ) -> float:
        """计算置信度分数"""
        base_score = 0.5

        # 证据项加分
        if evidence_items:
            high_evidence_count = sum(1 for item in evidence_items if item.evidence_level == "high")
            medium_evidence_count = sum(1 for item in evidence_items if item.evidence_level == "medium")
            base_score += (high_evidence_count * 0.1 + medium_evidence_count * 0.05)

        # 研究结论加分
        if research_findings:
            base_score += len(research_findings) * 0.02

        # 冲突项减分
        if conflicts:
            base_score -= len(conflicts) * 0.05

        return max(0.0, min(base_score, 1.0))

    def _determine_evidence_strength(self, confidence_score: float) -> str:
        """确定证据强度"""
        if confidence_score >= 0.8:
            return "strong"
        elif confidence_score >= 0.6:
            return "moderate"
        return "weak"

    def _determine_evidence_level(self, ref: Dict[str, Any]) -> str:
        """确定证据级别"""
        level = str(ref.get("evidence_level") or ref.get("confidence") or "low")
        if level in ["high", "strong", "verified"]:
            return "high"
        elif level in ["medium", "moderate", "mid"]:
            return "medium"
        return "low"

    def _determine_source_type(self, url: str) -> str:
        """确定来源类型"""
        lower_url = url.lower()
        if any(domain in lower_url for domain in [".edu", "academic", "research"]):
            return "academic_reference"
        elif any(domain in lower_url for domain in [".gov", "regulatory", "sec"]):
            return "regulatory_guidance"
        elif any(domain in lower_url for domain in ["aqr.com", "industry", "practitioner"]):
            return "industry_research"
        elif url:
            return "public_web"
        return "public_web"

    def _extract_conflicting_viewpoints(self, conflicts: List[ConflictNote]) -> List[str]:
        """提取冲突观点"""
        viewpoints = []
        for conflict in conflicts:
            viewpoints.extend(conflict.viewpoints)
        return viewpoints

    def _init_public_sources(self) -> Dict[str, Any]:
        """初始化公共来源库"""
        return {
            "academic": [
                "Fama/French Data Library",
                "Journal of Finance",
                "Journal of Financial Economics"
            ],
            "regulatory": [
                "SEC Investor Bulletins",
                "FINRA Investor Education",
                "CFTC Resources"
            ],
            "industry": [
                "AQR Insights",
                "BlackRock Research",
                "Vanguard Research"
            ]
        }


class QuantBeginnerResearchCollector:
    """量化入门研究采集器"""

    def __init__(self, standardizer: EvidenceStandardizer):
        self.standardizer = standardizer

    def collect_and_standardize_research(
        self,
        llm_result: Dict[str, Any],
        topic: str,
        symbol: str = "",
        timeframe: str = ""
    ) -> UnifiedOutputSchema:
        """采集并标准化研究结果"""

        # 标准化证据
        standardization_result = self.standardizer.standardize_llm_research_result(llm_result)

        # 构建统一输出schema
        return UnifiedOutputSchema(
            knowledge_sections=self._build_knowledge_sections(llm_result),
            research_conclusions=standardization_result.research_findings,
            execution_suggestions=self._extract_execution_suggestions(llm_result),
            risk_prompts=self._extract_risk_prompts(llm_result),
            timestamp=standardization_result.source_time,
            version="v1.0",
            assumptions=self._extract_assumptions(llm_result),
            invalidation_conditions=self._extract_invalidation_conditions(llm_result),
            sources=standardization_result.standardized_evidence,
            confidence_level=standardization_result.get_overall_confidence_level()
        )

    def _build_knowledge_sections(self, result: Dict[str, Any]) -> List[Any]:
        """构建知识说明部分"""
        # 这里需要从result中提取知识说明内容
        # 由于模型定义需要调整，暂时返回空列表
        return []

    def _extract_execution_suggestions(self, result: Dict[str, Any]) -> List[str]:
        """提取执行建议"""
        suggestions = []

        # 从practice_sequence提取
        practice_seq = result.get("practice_sequence", [])
        if isinstance(practice_seq, list):
            suggestions.extend(practice_seq)

        # 从minimum_viable_start提取
        min_start = result.get("minimum_viable_start", [])
        if isinstance(min_start, list):
            suggestions.extend(min_start)

        return suggestions

    def _extract_risk_prompts(self, result: Dict[str, Any]) -> List[str]:
        """提取风险提示"""
        risk_prompts = []

        # 从common_misunderstandings提取
        misunderstandings = result.get("common_misunderstandings", [])
        if isinstance(misunderstandings, list):
            for misunderstanding in misunderstandings:
                risk_prompts.append(f"Avoid: {misunderstanding}")

        return risk_prompts

    def _extract_assumptions(self, result: Dict[str, Any]) -> List[str]:
        """提取假设"""
        return ["Research assumes beginner-level knowledge and conservative risk tolerance"]

    def _extract_invalidation_conditions(self, result: Dict[str, Any]) -> List[str]:
        """提取失效条件"""
        conditions = []

        # 添加通用失效条件
        conditions.append("If market conditions change significantly, re-evaluate assumptions")
        conditions.append("If risk tolerance changes, adjust practice sequence accordingly")

        return conditions