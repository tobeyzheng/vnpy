from __future__ import annotations

import json
from typing import Any, Iterable

from vnpy_llm.base import beijing_now_isoformat

from .hub import EvaluationHub
from .models import (
    ConflictNote,
    EvidenceItem,
    ExplanationSection,
    PlanAssumption,
    PlanningArtifact,
    ResearchFinding,
    TerminologyItem,
)


BEGINNER_RESEARCH_SCHEMA_PRESET: dict[str, Any] = {
    "keys": [
        "topic",
        "key_findings",
        "recommendations",
        "risks",
        "conflicts",
        "low_confidence_items",
        "references",
    ],
    "system": (
        "You are QuantBeginnerResearchAgent. Output only one JSON object. "
        "Summarise public and verifiable knowledge about quantitative trading for a beginner. "
        "Separate verified findings, conflicting viewpoints, low-confidence items, and references. "
        "Do not fabricate URLs; when evidence is weak, mark evidence_level=low and verification_status=to_verify."
    ),
}


class BeginnerResearchService:
    def __init__(self, hub: EvaluationHub | None = None):
        self.hub = hub or EvaluationHub()

    def build_llm_request(
        self,
        *,
        topic: str,
        symbol: str = "",
        market: str = "",
        timeframe: str = "",
        extra_context: Any | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "topic": topic,
            "required_keys": BEGINNER_RESEARCH_SCHEMA_PRESET["keys"],
            "focus": [
                "core concepts",
                "research workflow",
                "risk control",
                "performance evaluation",
                "beginner-safe practice order",
            ],
            "runner_path": "scripts/classic_multifactor/run_llm_research.py",
            "schema_hint": "quant_trading",
        }
        if symbol:
            payload["symbol"] = symbol
        if market:
            payload["market"] = market
        if timeframe:
            payload["timeframe"] = timeframe
        if extra_context not in {None, "", []}:
            payload["context"] = extra_context
        return {
            "system_prompt": BEGINNER_RESEARCH_SCHEMA_PRESET["system"],
            "user_prompt": json.dumps(payload, ensure_ascii=False),
            "required_keys": list(BEGINNER_RESEARCH_SCHEMA_PRESET["keys"]),
            "runner_path": "scripts/classic_multifactor/run_llm_research.py",
        }

    def artifact_from_llm_result(
        self,
        result: dict[str, Any],
        *,
        generated_at: str | None = None,
        version: str = "v1",
    ) -> PlanningArtifact:
        generated_at = generated_at or beijing_now_isoformat()
        references = [self.normalize_reference(item) for item in self._as_list(result.get("references"))]
        findings = [self.normalize_finding(item) for item in self._as_list(result.get("key_findings"))]
        if not findings:
            findings = [
                ResearchFinding(
                    topic=str(result.get("topic") or "quant_trading"),
                    conclusion="No structured key_findings were returned; keep this result as a draft and verify manually.",
                    evidence_level="low",
                    source_kind="llm_summary",
                    confidence=0.35,
                    verification_status="to_verify",
                    invalidation_conditions=["No reliable public references were attached."],
                    references=references,
                )
            ]
        low_confidence_items = [
            self.normalize_finding({
                "topic": "low_confidence",
                "conclusion": text,
                "evidence_level": "low",
                "verification_status": "to_verify",
                "references": [],
                "invalidation_conditions": ["Re-check with public evidence before use."],
            })
            for text in self._flatten_text_items(result.get("low_confidence_items"))
        ]
        findings.extend(low_confidence_items)

        conflicts = [self._normalize_conflict(item) for item in self._as_list(result.get("conflicts"))]
        execution_suggestions = self._flatten_text_items(result.get("recommendations"))
        risk_prompts = self._flatten_text_items(result.get("risks"))
        invalidation_conditions: list[str] = []
        for finding in findings:
            invalidation_conditions.extend(finding.invalidation_conditions)
        if not references:
            invalidation_conditions.append("The current research result does not contain verifiable public references.")

        artifact = self.hub.build_planning_artifact(
            artifact_type="quant_trading_research",
            title=str(result.get("topic") or "Quant Trading Research"),
            generated_at=generated_at,
            version=version,
            assumptions=self._default_assumptions(),
            knowledge_sections=self._default_sections(),
            research_findings=findings,
            research_conclusions=[finding.conclusion for finding in findings],
            execution_suggestions=execution_suggestions,
            risk_prompts=risk_prompts,
            invalidation_conditions=sorted(set(invalidation_conditions)),
            evidence_items=references,
            conflicts=conflicts or self._default_conflicts(),
            meta={
                "source": "llm_normalized",
            "schema_hint": "quant_trading",
                "runner_path": "scripts/classic_multifactor/run_llm_research.py",
            },
        )
        return artifact

    def build_default_artifact(
        self,
        *,
        title: str = "Beginner Quant Research",
        generated_at: str | None = None,
        version: str = "v1",
    ) -> PlanningArtifact:
        generated_at = generated_at or beijing_now_isoformat()
        findings = self._default_findings()
        evidence = self.hub.merge_evidence(*(section.references for section in self._default_sections()))
        return self.hub.build_planning_artifact(
            artifact_type="quant_trading_research",
            title=title,
            generated_at=generated_at,
            version=version,
            assumptions=self._default_assumptions(),
            knowledge_sections=self._default_sections(),
            research_findings=findings,
            research_conclusions=[finding.conclusion for finding in findings],
            execution_suggestions=[
                "Start from research, explanation and backtest review before any simulation step.",
                "Use LLM results as an evidence summary, not as a direct execution instruction.",
                "Keep a small watchlist and define pause conditions before the first paper trade.",
            ],
            risk_prompts=[
                "Do not move to live trading until reconciliation, logging and approval controls are in place.",
                "If a result depends on minute-level execution, add stricter cost and latency assumptions.",
            ],
            invalidation_conditions=[
                "If you cannot explain why a rule should work, treat it as an observation idea instead of a trade rule.",
                "If transaction costs, slippage or liquidity assumptions are missing, do not upgrade to a higher stage.",
            ],
            evidence_items=evidence,
            conflicts=self._default_conflicts(),
            meta={
                "source": "curated_fallback",
            "schema_hint": "quant_trading",
                "runner_path": "scripts/classic_multifactor/run_llm_research.py",
            },
        )

    def normalize_reference(self, raw: Any) -> EvidenceItem:
        if isinstance(raw, str):
            return EvidenceItem(title=raw, evidence_level="low", note="String-only reference; verify manually.")
        if not isinstance(raw, dict):
            return EvidenceItem(title="unknown_reference", evidence_level="low", note="Unsupported reference format.")

        url = str(raw.get("url") or raw.get("source_url") or raw.get("link") or "")
        published_at = str(raw.get("published_at") or raw.get("date") or raw.get("time") or "")
        title = str(raw.get("title") or raw.get("name") or url or "untitled_reference")
        evidence_level = self._normalize_level(raw.get("evidence_level") or raw.get("confidence") or raw.get("level"))
        return EvidenceItem(
            title=title,
            source_url=url,
            published_at=published_at,
            source_type=self._infer_source_type(url),
            evidence_level=evidence_level,
            note=str(raw.get("note") or raw.get("summary") or ""),
        )

    def normalize_finding(self, raw: Any) -> ResearchFinding:
        if isinstance(raw, str):
            return ResearchFinding(
                topic="general",
                conclusion=raw,
                evidence_level="low",
                source_kind="llm_summary",
                confidence=0.4,
                verification_status="to_verify",
                invalidation_conditions=["No structured evidence item was attached."],
            )
        if not isinstance(raw, dict):
            return ResearchFinding(
                topic="general",
                conclusion="Unsupported finding format; verify manually.",
                evidence_level="low",
                source_kind="llm_summary",
                confidence=0.25,
                verification_status="to_verify",
                invalidation_conditions=["The original finding payload is not a valid object."],
            )

        references = [self.normalize_reference(item) for item in self._as_list(raw.get("references"))]
        evidence_level = self._normalize_level(raw.get("evidence_level") or raw.get("confidence") or "mid" if references else "low")
        confidence = raw.get("confidence")
        try:
            numeric_confidence = float(confidence)
            if numeric_confidence > 1:
                numeric_confidence = numeric_confidence / 100.0
        except (TypeError, ValueError):
            numeric_confidence = 0.75 if evidence_level == "high" else 0.6 if evidence_level == "mid" else 0.4
        verification_status = str(raw.get("verification_status") or ("verified" if references else "to_verify"))
        invalidation_conditions = self._flatten_text_items(raw.get("invalidation_conditions"))
        if not invalidation_conditions and not references:
            invalidation_conditions = ["No reliable public reference was attached."]
        return ResearchFinding(
            topic=str(raw.get("topic") or raw.get("theme") or "general"),
            conclusion=str(raw.get("conclusion") or raw.get("summary") or raw.get("finding") or ""),
            evidence_level=evidence_level,
            source_kind=str(raw.get("source_kind") or "public_research"),
            confidence=max(0.0, min(float(numeric_confidence), 1.0)),
            verification_status=verification_status,
            invalidation_conditions=invalidation_conditions,
            references=references,
        )

    def _default_assumptions(self) -> list[PlanAssumption]:
        return [
            PlanAssumption("evidence_scope", "public_and_verifiable_only", "Prefer public sources that can be revisited later."),
            PlanAssumption("default_frequency", "low_frequency_first", "Beginners should optimise for explainability and reviewability."),
            PlanAssumption("llm_role", "research_assistant_only", "LLM output should not bypass risk controls or directly place trades."),
        ]

    def _default_sections(self) -> list[ExplanationSection]:
        academic_ref = EvidenceItem(
            title="Fama/French Data Library",
            source_url="https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html",
            source_type="academic_reference",
            evidence_level="high",
            note="Canonical public factor data reference.",
        )
        aqr_ref = EvidenceItem(
            title="AQR Insights",
            source_url="https://www.aqr.com/Insights",
            source_type="industry_research",
            evidence_level="mid",
            note="Public practitioner research on factor investing and implementation trade-offs.",
        )
        sec_ref = EvidenceItem(
            title="SEC Investor Bulletin on Diversification",
            source_url="https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/diversification",
            source_type="regulatory_guidance",
            evidence_level="high",
            note="Simple public guidance on concentration risk.",
        )
        return [
            ExplanationSection(
                title="Core concepts",
                summary="Quantitative trading means turning an idea into explicit rules, testable data assumptions and repeatable review steps.",
                beginner_actions=[
                    "Write down the rule in plain language before coding it.",
                    "Keep the first strategy narrow enough that you can explain every input.",
                ],
                avoid_for_now=[
                    "Do not start from dozens of indicators and ad-hoc parameter tuning.",
                    "Do not treat a narrative or a chatbot summary as a trade signal by itself.",
                ],
                examples=[
                    "A simple example is: only observe symbols with stable liquidity, positive trend and a clear invalidation level.",
                ],
                warnings=[
                    "If you cannot describe why a rule should stop working, you do not understand the strategy boundary yet.",
                ],
                terms=[
                    TerminologyItem("factor", "A measurable input that may explain return differences.", "Momentum and quality are common factors.", "A factor is not a guaranteed edge forever."),
                    TerminologyItem("max drawdown", "The largest peak-to-trough loss during a period.", "A strategy up 20% but down 15% along the way still has a painful drawdown.", "High return with unmanageable drawdown is not beginner friendly."),
                    TerminologyItem("turnover", "How frequently the portfolio changes.", "Higher turnover usually means more cost and more execution sensitivity.", "High turnover is not automatically better."),
                ],
                references=[academic_ref, aqr_ref],
            ),
            ExplanationSection(
                title="Research workflow",
                summary="A safer workflow is hypothesis -> data check -> backtest -> simulation -> review, not idea -> live trade.",
                beginner_actions=[
                    "Record sample period, fee/slippage assumptions and what counts as a failure.",
                    "Split validation into at least development and out-of-sample review.",
                ],
                avoid_for_now=[
                    "Do not trust a single backtest run without checking another market regime.",
                ],
                examples=[
                    "A rule that looks great only in one strong trend year should stay in research or simulation.",
                ],
                warnings=[
                    "Missing data-quality checks can create false confidence through look-ahead bias or survivor bias.",
                ],
                terms=[
                    TerminologyItem("out-of-sample", "A period not used to design the rule.", "It is the closest thing to a fair exam for the strategy.", "Good in-sample performance does not imply good out-of-sample performance."),
                    TerminologyItem("slippage", "The difference between expected execution price and actual execution price.", "Fast intraday strategies are much more sensitive to slippage.", "Ignoring slippage can make a fragile strategy look good."),
                ],
                references=[academic_ref],
            ),
            ExplanationSection(
                title="Risk control",
                summary="For beginners, position sizing, diversification and pause rules are often more important than finding one more indicator.",
                beginner_actions=[
                    "Define single-position and portfolio exposure limits before the first paper trade.",
                    "Limit watchlist size so you can review every name properly.",
                ],
                avoid_for_now=[
                    "Do not increase risk because one recent trade worked well.",
                    "Do not concentrate several highly related names into one theme bet without noticing it.",
                ],
                examples=[
                    "Owning several AI-chip names can look diversified by ticker count but still be one theme exposure.",
                ],
                warnings=[
                    "If drawdown, execution anomaly or data anomaly is triggered, the next step should usually be review, not more trading.",
                ],
                terms=[
                    TerminologyItem("position sizing", "How much capital one idea is allowed to use.", "A good idea can still hurt if the size is too large.", "Entry direction and position size are separate decisions."),
                    TerminologyItem("capacity", "How much money a strategy can realistically handle.", "A minute-level strategy on illiquid names has lower capacity than a daily large-cap strategy.", "Capacity is not only about account size; it is also about liquidity."),
                ],
                references=[sec_ref, aqr_ref],
            ),
            ExplanationSection(
                title="Performance evaluation",
                summary="Headline return is incomplete; you need return, drawdown, risk-adjusted return, turnover, sample size and stability together.",
                beginner_actions=[
                    "Review both return and the path taken to achieve it.",
                    "Keep a small scorecard for every strategy version and compare changes carefully.",
                ],
                avoid_for_now=[
                    "Do not upgrade a strategy just because one parameter set has the highest profit.",
                ],
                examples=[
                    "A slightly lower return with much smaller drawdown and lower turnover may be the better beginner choice.",
                ],
                warnings=[
                    "If a strategy only wins after aggressive tuning, it may be overfit rather than robust.",
                ],
                terms=[
                    TerminologyItem("Sharpe ratio", "A rough measure of return per unit of volatility.", "Higher is usually better, but only when sample quality is acceptable.", "Sharpe alone does not capture tail risk or execution friction."),
                    TerminologyItem("stability", "Whether results stay reasonable across periods, assets or parameter ranges.", "A robust rule should not collapse after a small parameter change.", "The best single backtest number is not the same as stability."),
                ],
                references=[aqr_ref],
            ),
        ]

    def _default_findings(self) -> list[ResearchFinding]:
        refs = self.hub.merge_evidence(*(section.references for section in self._default_sections()))
        return [
            ResearchFinding(
                topic="workflow",
                conclusion="Beginners should prioritise evidence-backed, low-frequency and reviewable workflows over minute-level automation.",
                evidence_level="high",
                source_kind="curated_public_research",
                confidence=0.88,
                verification_status="verified",
                invalidation_conditions=["If the user already has proven minute-level execution infrastructure and monitoring, this may be relaxed."],
                references=refs[:2],
            ),
            ResearchFinding(
                topic="validation",
                conclusion="Out-of-sample checks, transaction costs and data-quality notes are mandatory before moving beyond research or backtest summaries.",
                evidence_level="high",
                source_kind="curated_public_research",
                confidence=0.9,
                verification_status="verified",
                invalidation_conditions=["Missing cost or data-quality assumptions should block stage upgrades."],
                references=refs[:2],
            ),
            ResearchFinding(
                topic="llm_usage",
                conclusion="LLM tools are useful for evidence summarisation and candidate explanation, but they should remain research helpers rather than execution authorities.",
                evidence_level="mid",
                source_kind="operational_constraint",
                confidence=0.82,
                verification_status="verified",
                invalidation_conditions=["If an LLM output bypasses rule-based screening or risk control, treat the workflow as unsafe."],
                references=refs[1:2],
            ),
            ResearchFinding(
                topic="risk_budget",
                conclusion="A written risk budget and stop conditions should exist before the first simulation session, not after the first drawdown.",
                evidence_level="mid",
                source_kind="curated_public_research",
                confidence=0.8,
                verification_status="verified",
                invalidation_conditions=["If the user cannot explain the stop rule, the plan is not ready for upgrade."],
                references=refs[2:3],
            ),
        ]

    def _default_conflicts(self) -> list[ConflictNote]:
        return [
            ConflictNote(
                topic="Low-frequency versus minute-level trading",
                viewpoints=[
                    "Low-frequency strategies are easier to explain, validate and review.",
                    "Minute-level strategies may react faster but become much more sensitive to cost, latency and operational errors.",
                ],
                applicability="Minute-level trading only becomes reasonable after data quality, logging and execution constraints are already reliable.",
                conservative_takeaway="For a new local system, stay with low-frequency research and simulation first.",
            ),
            ConflictNote(
                topic="Simple robust rules versus heavy parameter tuning",
                viewpoints=[
                    "Simple rules are easier to audit and less likely to hide curve fitting.",
                    "More parameters may improve in-sample fit but can reduce robustness.",
                ],
                applicability="Parameter tuning is useful only when the validation design is strong and the parameter surface stays stable.",
                conservative_takeaway="Prefer fewer parameters and stronger validation before adding complexity.",
            ),
        ]

    def _normalize_conflict(self, raw: Any) -> ConflictNote:
        if isinstance(raw, str):
            return ConflictNote(topic="unspecified_conflict", viewpoints=[raw], conservative_takeaway="Prefer the more conservative interpretation until verified.")
        if not isinstance(raw, dict):
            return ConflictNote(topic="unsupported_conflict", viewpoints=["Unsupported conflict payload"], conservative_takeaway="Verify manually.")
        viewpoints = self._flatten_text_items(raw.get("viewpoints") or raw.get("claims") or raw.get("items"))
        return ConflictNote(
            topic=str(raw.get("topic") or "general_conflict"),
            viewpoints=viewpoints,
            applicability=str(raw.get("applicability") or raw.get("scope") or ""),
            conservative_takeaway=str(raw.get("conservative_takeaway") or raw.get("takeaway") or "Prefer the conservative interpretation until verified."),
        )

    def _infer_source_type(self, url: str) -> str:
        lower = url.lower()
        if "dartmouth.edu" in lower or ".edu" in lower:
            return "academic_reference"
        if "sec.gov" in lower or "investor.gov" in lower:
            return "regulatory_guidance"
        if "aqr.com" in lower:
            return "industry_research"
        if lower:
            return "public_web"
        return "public_web"

    def _normalize_level(self, value: Any) -> str:
        if isinstance(value, (int, float)):
            score = float(value)
            if score > 1:
                score = score / 100.0
            if score >= 0.8:
                return "high"
            if score >= 0.55:
                return "mid"
            return "low"
        text = str(value or "").strip().lower()
        if text in {"high", "strong", "verified"}:
            return "high"
        if text in {"mid", "medium", "moderate"}:
            return "mid"
        return "low"

    def _as_list(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _flatten_text_items(self, value: Any) -> list[str]:
        items: list[str] = []
        for row in self._as_list(value):
            if isinstance(row, str):
                text = row.strip()
                if text:
                    items.append(text)
            elif isinstance(row, dict):
                for key in ("summary", "text", "title", "item", "step", "recommendation", "conclusion"):
                    text = str(row.get(key) or "").strip()
                    if text:
                        items.append(text)
                        break
        return items
