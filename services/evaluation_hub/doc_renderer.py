from __future__ import annotations

import json
from datetime import datetime, timezone

from .models import ExplanationSection, PlanningArtifact, RenderedDocument, TerminologyItem


class BeginnerExplanationRenderer:
    def render_markdown(self, artifact: PlanningArtifact, *, version: str | None = None) -> RenderedDocument:
        generated_at = datetime.now(timezone.utc).isoformat()
        version = version or artifact.version
        lines: list[str] = []
        lines.append(f"### {artifact.title}")
        lines.append("")
        lines.append(f"- Generated at: {generated_at}")
        lines.append(f"- Version: {version}")
        lines.append("- Audience: beginner quant trader")
        lines.append("")
        lines.append("### What this note is trying to do")
        lines.append("- Explain the key ideas in plain language.")
        lines.append("- Tell you what to do first, what to avoid for now, and what must be verified before upgrading stages.")
        lines.append("- Keep research, execution advice and risk prompts separate.")
        lines.append("")
        lines.append("### Learning order")
        for item in self._learning_order(artifact):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### Practice order")
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
        generated_at = datetime.now(timezone.utc).isoformat()
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
            "practice_order": self._practice_order(artifact),
            "assumptions": self._assumption_lines(artifact),
            "common_pitfalls": self._common_pitfalls(artifact),
            "minimum_viable_start": self._minimum_viable_start(artifact),
            "readiness": self._readiness_lines(artifact),
            "plan_differences": self._plan_difference_lines(artifact),
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

    def rewrite_section_for_beginner(self, section: ExplanationSection, *, extra_hint: str = "") -> ExplanationSection:
        summary = section.summary.strip()
        if extra_hint:
            summary = f"{summary} In plain language: {extra_hint.strip()}"
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
            "Understand core concepts and risk language before touching execution settings.",
            "Learn the research workflow: hypothesis -> data check -> backtest -> simulation -> review.",
            "Study a small candidate set and explain why each name is on the list.",
            "Write a risk budget and pause rules before any simulation session.",
        ]
        if artifact.capability_gaps:
            base.append("Notice the current local capability gaps before planning a higher-risk stage.")
        return base

    def _practice_order(self, artifact: PlanningArtifact) -> list[str]:
        actions = [
            "Run the read-only health check first.",
            "Review research findings and evidence levels.",
            "Check the watchlist or candidate observations and keep only a few names.",
            "Review one backtest or validation summary before any simulation idea.",
            "Move to simulation only after risk budget and readiness checklist are both clear.",
        ]
        if artifact.plan_phases:
            actions.append("Follow the phase order in the generated personal plan instead of skipping directly to execution.")
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
            "Looking only at return and ignoring drawdown, turnover and sample quality.",
            "Adding too many correlated names and calling it diversification.",
            "Letting an LLM summary replace manual review of evidence and risk controls.",
        ]
        pitfalls.extend(artifact.risk_prompts[:2])
        return list(dict.fromkeys(pitfalls))

    def _minimum_viable_start(self, artifact: PlanningArtifact) -> list[str]:
        start = [
            "One market, a small watchlist and a low-frequency review habit.",
            "A written risk budget with single-position, total-exposure and pause rules.",
            "At least one backtest or validation note with data-quality and cost assumptions recorded.",
            "A simulation observation period before any live intent.",
        ]
        if artifact.candidate_observations:
            start.append("Use the top few beginner_watchlist or observe_only names as the initial review universe.")
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

    def _section_to_dict(self, section: ExplanationSection) -> dict:
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
