from __future__ import annotations

import json
from typing import Any

from vnpy_llm.base import beijing_now_isoformat

from .models import ExplanationSection, PlanningArtifact, RenderedDocument, TerminologyItem, UnifiedOutputSchema
from .evidence_standardizer import EvidenceStandardizer


class TradingExplanationRenderer:
    def render_markdown(self, artifact: PlanningArtifact, *, version: str | None = None) -> RenderedDocument:
        generated_at = beijing_now_isoformat()
        version = version or artifact.version
        lines: list[str] = []
        lines.append(f"### {artifact.title}")
        lines.append("")
        lines.append(f"- Generated at: {generated_at}")
        lines.append(f"- Version: {version}")
        lines.append("- Audience: trading workflow reviewer")
        lines.append("")
        lines.append("### What this note is trying to do")
        lines.append("- Explain the current trading evidence, assumptions, and stage gates in plain language.")
        lines.append("- Separate research conclusions, execution suggestions, and risk prompts so the workflow remains auditable.")
        lines.append("- Show what changed, what remains blocked, and what can be acted on next.")
        lines.append("")
        lines.append("### Review order")
        for item in self._learning_order(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Workflow order")
        for item in self._practice_order(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Current assumptions")
        for item in self._assumption_lines(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Knowledge sections")
        for section in artifact.knowledge_sections:
            lines.extend(self._render_section(section))
        lines.append("")
        lines.append("### Research conclusions")
        if artifact.research_findings:
            for finding in artifact.research_findings:
                lines.append(f"- [{finding.evidence_level}] {finding.conclusion}")
        else:
            for finding in artifact.research_conclusions:
                lines.append(f"- {finding}")
        lines.append("")
        lines.append("### Common pitfalls")
        for item in self._common_pitfalls(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Minimum viable start")
        for item in self._minimum_viable_start(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Readiness checkpoints")
        for item in self._readiness_lines(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### What changed from the previous plan")
        for item in self._plan_difference_lines(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Personalization summary")
        for item in self._personalization_lines(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Update scope")
        for item in self._update_scope_lines(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Next actions")
        for item in self._next_steps(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Local system boundaries")
        for item in self._local_limits(artifact):
            lines.append(f"- {item}")
        body = "\n".join(lines).strip() + "\n"
        return RenderedDocument(
            title=f"{artifact.title} (markdown)",
            format="markdown",
            body=body,
            section_count=max(len(artifact.knowledge_sections), 1) + 9,
            version=version,
            generated_at=generated_at,
        )

    def render_json(self, artifact: PlanningArtifact, *, version: str | None = None) -> RenderedDocument:
        generated_at = beijing_now_isoformat()
        version = version or artifact.version
        payload = {
            "title": artifact.title,
            "version": version,
            "generated_at": generated_at,
            "knowledge_sections": [self._section_to_dict(section) for section in artifact.knowledge_sections],
            "research_findings": [
                {
                    "topic": finding.topic,
                    "conclusion": finding.conclusion,
                    "evidence_level": finding.evidence_level,
                    "verification_status": finding.verification_status,
                    "invalidation_conditions": list(finding.invalidation_conditions),
                }
                for finding in artifact.research_findings
            ],
            "workflow_order": self._practice_order(artifact),
            "assumptions": self._assumption_lines(artifact),
            "common_pitfalls": self._common_pitfalls(artifact),
            "minimum_viable_start": self._minimum_viable_start(artifact),
            "readiness": self._readiness_lines(artifact),
            "plan_differences": self._plan_difference_lines(artifact),
            "personalization_summary": self._personalization_lines(artifact),
            "update_scope": self._update_scope_lines(artifact),
            "next_actions": self._next_steps(artifact),
            "local_limits": self._local_limits(artifact),
        }
        return RenderedDocument(
            title=f"{artifact.title} (json)",
            format="json",
            body=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            section_count=max(len(artifact.knowledge_sections), 1) + 8,
            version=version,
            generated_at=generated_at,
        )

    def rewrite_section_for_trading(self, section: ExplanationSection, *, extra_hint: str = "") -> ExplanationSection:
        summary = section.summary.strip()
        if extra_hint:
            summary = f"{summary} Trading context: {extra_hint.strip()}"
        if not summary.endswith("."):
            summary = summary + "."
        rewritten_terms = [self._rewrite_term(term) for term in section.terms]
        rewritten_examples = list(section.examples) or ["Start with one simple rule and write down when it should fail."]
        rewritten_warnings = list(section.warnings) or ["If you cannot explain the step, keep it in research mode."]
        rewritten_actions = list(section.beginner_actions) or ["Turn this section into one checklist item you can verify locally."]
        rewritten_avoid = list(section.avoid_for_now) or ["Avoid adding more indicators before you can explain the existing ones."]
        return ExplanationSection(
            title=section.title,
            summary=summary,
            beginner_actions=rewritten_actions,
            avoid_for_now=rewritten_avoid,
            examples=rewritten_examples,
            warnings=rewritten_warnings,
            terms=rewritten_terms,
            references=list(section.references),
        )

    def _render_section(self, section: ExplanationSection) -> list[str]:
        lines = [f"#### {section.title}", f"- Summary: {section.summary}"]
        if section.beginner_actions:
            lines.append("- What you can do now:")
            for item in section.beginner_actions:
                lines.append(f"  - {item}")
        if section.avoid_for_now:
            lines.append("- What to avoid for now:")
            for item in section.avoid_for_now:
                lines.append(f"  - {item}")
        if section.examples:
            lines.append("- Examples:")
            for item in section.examples:
                lines.append(f"  - {item}")
        if section.warnings:
            lines.append("- Warnings:")
            for item in section.warnings:
                lines.append(f"  - {item}")
        if section.terms:
            lines.append("- Key terms:")
            for term in section.terms:
                lines.append(f"  - {term.term}: {term.definition}")
                if term.example:
                    lines.append(f"    - Example: {term.example}")
                if term.common_misunderstanding:
                    lines.append(f"    - Common misunderstanding: {term.common_misunderstanding}")
        return lines

    def _learning_order(self, artifact: PlanningArtifact) -> list[str]:
        base = [
            "Understand the current evidence set and risk language before touching execution settings.",
            "Review the workflow in order: candidate preparation -> healthcheck -> candidate framework -> backtest -> readiness.",
            "Check the active trade universe and understand why each name remains in its current bucket.",
            "Verify risk budget and pause rules before any simulation or live-adjacent step.",
        ]
        if artifact.capability_gaps:
            base.append("Notice the current local capability gaps before planning a higher-risk stage.")
        return base

    def _practice_order(self, artifact: PlanningArtifact) -> list[str]:
        actions = [
            "Run the read-only health check first.",
            "Review research findings and evidence levels.",
            "Check the candidate buckets and keep only the intended trade-universe names active.",
            "Review one backtest or validation summary before any simulation idea.",
            "Move to simulation or live readiness only after risk budget and readiness checklist are both clear.",
        ]
        if artifact.plan_phases:
            actions.append("Follow the generated phase order instead of skipping directly to execution.")
        return actions

    def _assumption_lines(self, artifact: PlanningArtifact) -> list[str]:
        if not artifact.assumptions:
            return ["No explicit assumptions were recorded for this artifact."]
        return [
            f"{item.name}: {item.value} — {item.reason}" if item.reason else f"{item.name}: {item.value}"
            for item in artifact.assumptions
        ]

    def _common_pitfalls(self, artifact: PlanningArtifact) -> list[str]:
        pitfalls = [
            "Confusing a good story with a tested rule.",
            "Looking only at return and ignoring drawdown, turnover, and sample quality.",
            "Adding too many correlated names and calling it diversification.",
            "Letting an LLM summary replace manual review of evidence and risk controls.",
        ]
        pitfalls.extend(artifact.risk_prompts[:2])
        return list(dict.fromkeys(pitfalls))

    def _minimum_viable_start(self, artifact: PlanningArtifact) -> list[str]:
        start = [
            "One market, a focused trade universe, and a stable review habit.",
            "A written risk budget with single-position, total-exposure, and pause rules.",
            "At least one backtest or validation note with data-quality and cost assumptions recorded.",
            "A simulation observation period before any live intent.",
        ]
        if artifact.candidate_observations:
            start.append("Use the top few priority_trade or active_watch names as the initial review universe.")
        return start

    def _readiness_lines(self, artifact: PlanningArtifact) -> list[str]:
        if artifact.readiness is None:
            return ["No readiness checklist has been attached yet."]
        failed = artifact.readiness.failed_items()
        if not failed:
            return [f"Stage {artifact.readiness.stage}: all tracked readiness checks passed."]
        lines = [f"Stage {artifact.readiness.stage}: {len(failed)} readiness items still need work."]
        for item in failed:
            detail = item.details or "Check not passed."
            remediation = f" Remediation: {item.remediation}" if item.remediation else ""
            lines.append(f"{item.name} [{item.severity}] — {detail}{remediation}")
        return lines

    def _plan_difference_lines(self, artifact: PlanningArtifact) -> list[str]:
        diffs = list(artifact.meta.get("plan_differences") or [])
        if not diffs:
            if artifact.meta.get("previous_plan_loaded"):
                return ["A previous plan was loaded, but no tracked assumptions or risk-budget fields changed."]
            return ["No previous plan comparison was available for this run."]
        return [
            f"{item.get('field')}: {item.get('old')} -> {item.get('new')}"
            for item in diffs
        ]

    def _personalization_lines(self, artifact: PlanningArtifact) -> list[str]:
        summary = dict(artifact.meta.get("personalization_summary") or {})
        if not summary:
            return ["No explicit personalization summary was recorded for this artifact."]
        return [f"{key}: {value}" for key, value in summary.items()]

    def _update_scope_lines(self, artifact: PlanningArtifact) -> list[str]:
        scopes = list(artifact.meta.get("profile_update_scope") or [])
        if not scopes:
            return ["No scoped profile update information was recorded."]
        return scopes

    def _next_steps(self, artifact: PlanningArtifact) -> list[str]:
        suggestions = list(artifact.meta.get("next_step_suggestions") or [])
        if not suggestions:
            suggestions = list(artifact.execution_suggestions)
        if artifact.readiness and not artifact.readiness.passed:
            suggestions.append("Do not upgrade stages until the failed readiness items are resolved.")
        return list(dict.fromkeys(suggestions)) or ["Review the latest artifact before changing the workflow."]

    def _local_limits(self, artifact: PlanningArtifact) -> list[str]:
        limits: list[str] = []
        for gap in artifact.capability_gaps:
            limits.append(f"Missing local capability: {gap.expected_path} -> {gap.manual_alternative or gap.reason}")
        if not limits:
            limits.append("Execution-related stages still require explicit confirmation and should default to plan-only mode.")
        confirmation_required = [cap.path for cap in artifact.capability_map if cap.requires_confirmation]
        if confirmation_required:
            limits.append("The following local entries require confirmation before execution: " + ", ".join(sorted(confirmation_required)[:5]))
        return limits

    def _section_to_dict(self, section: ExplanationSection) -> dict[str, Any]:
        return {
            "title": section.title,
            "summary": section.summary,
            "beginner_actions": list(section.beginner_actions),
            "avoid_for_now": list(section.avoid_for_now),
            "examples": list(section.examples),
            "warnings": list(section.warnings),
            "terms": [
                {
                    "term": term.term,
                    "definition": term.definition,
                    "example": term.example,
                    "common_misunderstanding": term.common_misunderstanding,
                }
                for term in section.terms
            ],
        }

    def _rewrite_term(self, term: TerminologyItem) -> TerminologyItem:
        definition = term.definition.strip()
        if not definition.endswith("."):
            definition += "."
        return TerminologyItem(
            term=term.term,
            definition=definition,
            example=term.example or "Use a small real example you can explain without jargon.",
            common_misunderstanding=term.common_misunderstanding or "Do not treat the term as a magic shortcut to profit.",
        )


class QuantTradingDocumentRenderer:
    """量化交易文档渲染器"""

    def __init__(self, standardizer: EvidenceStandardizer | None = None):
        self.standardizer = standardizer or EvidenceStandardizer()
        self.trading_renderer = TradingExplanationRenderer()

    def render_trading_guide(
        self,
        research_result: UnifiedOutputSchema,
        *,
        include_evidence_summary: bool = True,
        include_conflict_analysis: bool = True,
        include_rewritten_chapters: bool = True,
    ) -> RenderedDocument:
        generated_at = beijing_now_isoformat()
        lines: list[str] = []
        title = getattr(research_result, "title", "Quantitative Trading Guide")
        meta = dict(getattr(research_result, "meta", {}) or {})
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"- Generated: {generated_at}")
        lines.append("- Audience: trading workflow reviewer")
        lines.append(f"- Confidence Level: {research_result.confidence_level or 'medium'}")
        lines.append("")
        lines.append("### Core Concepts")
        if research_result.knowledge_sections:
            for section in research_result.knowledge_sections:
                lines.extend(self._render_section(section))
        else:
            lines.append("- Core concepts will be populated after research collection")
        lines.append("")
        lines.append("### Review Sequence")
        learning_seq = meta.get("learning_sequence") or self._default_learning_sequence()
        for i, item in enumerate(learning_seq, 1):
            lines.append(f"{i}. {item}")
        lines.append("")
        lines.append("### Workflow Sequence")
        practice_seq = meta.get("practice_sequence") or self._default_practice_sequence()
        for i, item in enumerate(practice_seq, 1):
            lines.append(f"{i}. {item}")
        lines.append("")
        if include_evidence_summary:
            lines.append("### Evidence Summary")
            lines.extend(self._render_evidence_summary(research_result))
            lines.append("")
        if include_conflict_analysis:
            lines.append("### Conflicting Viewpoints")
            lines.extend(self._render_conflict_analysis(research_result, meta=meta))
            lines.append("")
        if include_rewritten_chapters:
            lines.append("### Trading-Focused Explanations")
            lines.extend(self._render_rewritten_chapters(research_result))
            lines.append("")
        lines.append("### Risk Controls")
        lines.extend(self._render_risk_controls(research_result))
        lines.append("")
        lines.append("### Minimum Viable Starting Point")
        min_start = meta.get("minimum_viable_start") or self._default_minimum_start()
        for item in min_start:
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Common Misunderstandings")
        misunderstandings = meta.get("common_misunderstandings") or self._default_misunderstandings()
        for item in misunderstandings:
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Next Steps")
        lines.extend(self._render_next_steps(research_result))
        body = "\n".join(lines).strip() + "\n"
        return RenderedDocument(
            title=f"{title} (Markdown)",
            format="markdown",
            body=body,
            section_count=self._count_sections(lines),
            version="v1.0",
            generated_at=generated_at,
        )

    def _render_section(self, section: ExplanationSection) -> list[str]:
        lines = [f"#### {section.title}", section.summary]
        if section.beginner_actions:
            lines.append("- What you can do now:")
            for action in section.beginner_actions:
                lines.append(f"  - {action}")
        if section.avoid_for_now:
            lines.append("- What to avoid for now:")
            for avoid in section.avoid_for_now:
                lines.append(f"  - {avoid}")
        if section.examples:
            lines.append("- Examples:")
            for example in section.examples:
                lines.append(f"  - {example}")
        if section.warnings:
            lines.append("- Warnings:")
            for warning in section.warnings:
                lines.append(f"  - {warning}")
        if section.terms:
            lines.append("- Key Terms:")
            for term in section.terms:
                lines.append(f"  - {term.term}: {term.definition}")
                if term.example:
                    lines.append(f"    - Example: {term.example}")
        lines.append("")
        return lines

    def _render_evidence_summary(self, research: UnifiedOutputSchema) -> list[str]:
        lines: list[str] = []
        if research.sources:
            high_evidence = [s for s in research.sources if getattr(s, "evidence_level", "medium") == "high"]
            medium_evidence = [s for s in research.sources if getattr(s, "evidence_level", "medium") == "medium"]
            low_evidence = [s for s in research.sources if getattr(s, "evidence_level", "medium") == "low"]
            lines.append(f"- High confidence sources: {len(high_evidence)}")
            lines.append(f"- Medium confidence sources: {len(medium_evidence)}")
            lines.append(f"- Low confidence sources: {len(low_evidence)}")
        if research.research_conclusions:
            lines.append(f"- Research conclusions: {len(research.research_conclusions)}")
        return lines or ["- No evidence summary is available yet."]

    def _render_conflict_analysis(self, research: UnifiedOutputSchema, *, meta: dict[str, Any]) -> list[str]:
        conflicts = meta.get("conflicting_viewpoints", [])
        if conflicts:
            return ["Different schools of thought exist on these topics:", *[f"- {conflict}" for conflict in conflicts]]
        return ["No major conflicting viewpoints identified in current research."]

    def _render_rewritten_chapters(self, research: UnifiedOutputSchema) -> list[str]:
        lines: list[str] = []
        if research.knowledge_sections:
            for section in research.knowledge_sections:
                rewritten = self.trading_renderer.rewrite_section_for_trading(section)
                lines.append(f"#### {rewritten.title}")
                lines.append(rewritten.summary)
                if rewritten.beginner_actions:
                    lines.append("- Trading Actions:")
                    for action in rewritten.beginner_actions:
                        lines.append(f"  - {action}")
                lines.append("")
        return lines or ["- No rewritten chapters are available yet."]

    def _render_risk_controls(self, research: UnifiedOutputSchema) -> list[str]:
        risk_prompts = research.risk_prompts or []
        if risk_prompts:
            return ["- Essential Risk Controls:", *[f"  - {prompt}" for prompt in risk_prompts]]
        return [
            "- Default Risk Controls:",
            "  - Start with simulation or paper execution only",
            "  - Limit position size to a controlled fraction of portfolio risk",
            "  - Set maximum daily and portfolio drawdown limits",
            "  - Keep execution and readiness artifacts auditable",
        ]

    def _render_next_steps(self, research: UnifiedOutputSchema) -> list[str]:
        execution_suggestions = research.execution_suggestions or []
        if execution_suggestions:
            return ["- Recommended Next Steps:", *[f"  - {suggestion}" for suggestion in execution_suggestions]]
        return [
            "- Suggested Next Steps:",
            "  - Review core concepts and terminology",
            "  - Audit the candidate and backtest evidence",
            "  - Refresh risk management assumptions",
            "  - Revisit readiness gates before moving forward",
        ]

    def _default_learning_sequence(self) -> list[str]:
        return [
            "Understand market mechanics and trading terminology",
            "Review asset-class differences and execution constraints",
            "Study risk management principles and position sizing",
            "Understand strategy evidence, validation, and backtesting",
            "Review readiness gates before simulation or live escalation",
        ]

    def _default_practice_sequence(self) -> list[str]:
        return [
            "Audit the current candidate universe",
            "Review one strategy and its invalidation logic",
            "Backtest on historical data with costs recorded",
            "Validate risk management with position sizing",
            "Review and analyze execution-readiness artifacts",
        ]

    def _default_minimum_start(self) -> list[str]:
        return [
            "Basic understanding of financial markets",
            "Access to historical market data",
            "Simulation or paper-trading setup",
            "Simple strategy backtesting capability",
            "Risk management framework",
        ]

    def _default_misunderstandings(self) -> list[str]:
        return [
            "More complex strategies are always better",
            "Past performance guarantees future results",
            "Automated trading eliminates all risk",
            "Readiness artifacts can replace manual review",
            "You need to widen the universe before the current routine is stable",
        ]

    def _count_sections(self, lines: list[str]) -> int:
        return sum(1 for line in lines if line.startswith("### "))


BeginnerExplanationRenderer = TradingExplanationRenderer
QuantBeginnerDocumentRenderer = QuantTradingDocumentRenderer
