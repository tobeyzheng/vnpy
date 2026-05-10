from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .models import CandidateObservation


SUPPORTED_BEGINNER_MARKETS = {"us", "hong_kong"}
HIGH_RISK_KEYWORDS = (
    "高波动",
    "波动较大",
    "高beta",
    "估值偏高",
    "涨速过快",
    "theme volatility",
    "volatility",
    "speculative",
)
MEDIUM_RISK_KEYWORDS = (
    "回撤",
    "竞争",
    "情绪",
    "政策",
    "regulation",
    "execution",
    "latency",
    "估值",
)
THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ai_compute": ("AI", "算力", "chip", "芯片", "半导体", "semiconductor"),
    "platform_internet": ("平台", "互联网", "广告", "游戏", "platform", "consumer internet"),
    "consumer_growth": ("消费", "汽车", "销量", "消费电子", "robot", "机器人"),
    "financial_defensive": ("保险", "交易所", "回购", "dividend", "cash flow"),
}


class BeginnerCandidateFramework:
    def framework_rules(self) -> dict[str, list[str]]:
        return {
            "stock_pool_scope": [
                "Prefer large or medium-large liquid names that a beginner can follow repeatedly.",
                "Use a small cross-checkable watchlist instead of a broad high-turnover universe.",
            ],
            "liquidity_rules": [
                "If real liquidity data is missing locally, require a manual quote snapshot check before any upgrade.",
                "Prefer names already present in the local candidate inputs and widely covered by the current pipeline.",
            ],
            "volatility_rules": [
                "Downgrade high-volatility or theme-heavy names to observe_only or validate_only.",
                "Use stricter limits when the rationale depends on event bursts or fast sentiment changes.",
            ],
            "diversification_rules": [
                "Avoid stacking too many names from the same theme bucket.",
                "Keep the beginner watchlist short enough that each name can be reviewed manually.",
            ],
            "exclusions": [
                "Exclude low-score candidates from the first watchlist.",
                "Downgrade unsupported markets or missing-data names to validate_only.",
            ],
        }

    def build_observation_list(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        preferred_markets: list[str] | None = None,
        max_items: int = 5,
    ) -> list[CandidateObservation]:
        preferred = set(preferred_markets or [])
        ranked = sorted(list(rows), key=self._rank_score, reverse=True)
        theme_counts: dict[str, int] = defaultdict(int)
        observations: list[CandidateObservation] = []

        for row in ranked:
            if len(observations) >= max_items:
                break
            market = str(row.get("market") or "")
            theme = self._infer_theme(row)
            risk_level = self._risk_level(row)
            supported_market = market in SUPPORTED_BEGINNER_MARKETS
            selected_as = self._selected_as(row, risk_level=risk_level, supported_market=supported_market, preferred_market=(not preferred or market in preferred))
            if theme_counts[theme] >= 2 and selected_as == "beginner_watchlist":
                selected_as = "observe_only"
            if selected_as == "validate_only" and len(observations) >= max(2, max_items - 1):
                continue
            observation = CandidateObservation(
                symbol=str(row.get("symbol") or ""),
                market=market,
                selected_as=selected_as,
                score=round(self._rank_score(row), 2),
                reasons=self._reasons(row, theme),
                primary_risks=self._primary_risks(row, risk_level, supported_market),
                validation_points=self._validation_points(row, risk_level, supported_market),
                meta={
                    "theme": theme,
                    "action_hint": row.get("action_hint"),
                    "framework_rules": self.framework_rules(),
                    "supported_market": supported_market,
                },
            )
            theme_counts[theme] += 1
            observations.append(observation)
        return observations

    def suggest_next_actions(self, observations: Iterable[CandidateObservation]) -> list[str]:
        actions: list[str] = []
        for item in observations:
            if item.selected_as == "beginner_watchlist":
                actions.append(f"Review {item.symbol} on a low-frequency schedule and define a clear invalidation point before simulation.")
            elif item.selected_as == "observe_only":
                actions.append(f"Keep {item.symbol} as an observation name until volatility and execution assumptions are clearer.")
            else:
                actions.append(f"Use {item.symbol} as a validation-only example; do not treat it as an executable signal yet.")
        return list(dict.fromkeys(actions))

    def summary(self, observations: Iterable[CandidateObservation]) -> dict[str, Any]:
        rows = list(observations)
        return {
            "counts": {
                "beginner_watchlist": sum(1 for row in rows if row.selected_as == "beginner_watchlist"),
                "observe_only": sum(1 for row in rows if row.selected_as == "observe_only"),
                "validate_only": sum(1 for row in rows if row.selected_as == "validate_only"),
            },
            "rules": self.framework_rules(),
        }

    def _rank_score(self, row: dict[str, Any]) -> float:
        raw_score = float(row.get("raw_score") or 0.0) * 100.0
        signals = row.get("signals") or []
        signal_bonus = max((float(item.get("score") or 0.0) for item in signals if isinstance(item, dict)), default=0.0) * 10.0
        return raw_score + signal_bonus

    def _selected_as(
        self,
        row: dict[str, Any],
        *,
        risk_level: str,
        supported_market: bool,
        preferred_market: bool,
    ) -> str:
        score = self._rank_score(row)
        if not supported_market:
            return "validate_only"
        if not preferred_market:
            return "observe_only"
        if score >= 84 and risk_level == "low":
            return "beginner_watchlist"
        if score >= 76 and risk_level in {"low", "medium"}:
            return "observe_only"
        return "validate_only"

    def _infer_theme(self, row: dict[str, Any]) -> str:
        text = " ".join(
            str(row.get(key) or "")
            for key in ("name", "rationale", "risk", "action_hint")
        )
        for theme, keywords in THEME_KEYWORDS.items():
            if any(keyword.lower() in text.lower() for keyword in keywords):
                return theme
        return "general"

    def _risk_level(self, row: dict[str, Any]) -> str:
        text = " ".join(str(row.get(key) or "") for key in ("risk", "rationale", "action_hint"))
        lower = text.lower()
        if any(keyword.lower() in lower for keyword in HIGH_RISK_KEYWORDS):
            return "high"
        if any(keyword.lower() in lower for keyword in MEDIUM_RISK_KEYWORDS):
            return "medium"
        return "low"

    def _reasons(self, row: dict[str, Any], theme: str) -> list[str]:
        reasons: list[str] = []
        rationale = str(row.get("rationale") or "").strip()
        if rationale:
            reasons.append(rationale)
        for signal in row.get("signals") or []:
            if isinstance(signal, dict):
                summary = str(signal.get("summary") or "").strip()
                if summary:
                    reasons.append(summary)
        reasons.append(f"Theme bucket: {theme}")
        return list(dict.fromkeys(reasons))[:4]

    def _primary_risks(self, row: dict[str, Any], risk_level: str, supported_market: bool) -> list[str]:
        risks: list[str] = []
        base_risk = str(row.get("risk") or "").strip()
        if base_risk:
            risks.append(base_risk)
        if risk_level == "high":
            risks.append("High volatility or narrative-driven price action can make execution and review harder for a beginner.")
        if not supported_market:
            risks.append("This market is not covered by the current beginner-safe automated execution path.")
        return list(dict.fromkeys(risks))[:4]

    def _validation_points(self, row: dict[str, Any], risk_level: str, supported_market: bool) -> list[str]:
        points = [
            "Confirm current liquidity and spread with a fresh quote snapshot before any stage upgrade.",
            "Check whether the thesis still fits a low-frequency review process.",
            "Write down one concrete invalidation condition for the name.",
        ]
        if risk_level != "low":
            points.append("Review whether recent volatility makes the name unsuitable for a beginner-sized position.")
        if not supported_market:
            points.append("Keep the name in research or validation mode until a matching local execution path exists.")
        return points
