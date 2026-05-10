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
MINUTE_LEVEL_KEYWORDS = (
    "intraday",
    "分钟",
    "minute",
    "scalp",
    "open",
    "opening range",
    "session",
    "breakout",
    "tape",
    "日内",
)
DAILY_LEVEL_KEYWORDS = (
    "swing",
    "pullback",
    "trend",
    "低频",
    "天级别",
    "daily",
    "position trade",
    "weekly review",
    "cash flow",
    "buyback",
)
LOW_LIQUIDITY_KEYWORDS = (
    "illiquid",
    "low liquidity",
    "wide spread",
    "thin",
    "流动性不足",
    "缺少流动性",
)


class BeginnerCandidateFramework:
    def framework_rules(self) -> dict[str, list[str]]:
        return {
            "stock_pool_scope": [
                "Prefer liquid names that a reviewer can explain and monitor repeatedly.",
                "Use a small watchlist and keep candidate outputs directly reusable by backtest and readiness stages.",
            ],
            "liquidity_rules": [
                "If local liquidity comfort is weak, downgrade the candidate or mark cadence as needs_review.",
                "Prefer minute-level candidates only when turnover, signal freshness, and execution context all support it.",
            ],
            "cadence_rules": [
                "Use daily cadence as the default when a symbol can be monitored with a low-frequency review routine.",
                "Promote a symbol to minute cadence only when the input explicitly supports intraday handling.",
                "Use needs_review when the current local data is not enough to justify daily or minute cadence.",
            ],
            "volatility_rules": [
                "Downgrade high-volatility or theme-heavy names to observe_only or validate_only.",
                "Keep minute-level classification conservative when volatility is high and execution comfort is weak.",
            ],
            "diversification_rules": [
                "Avoid stacking too many names from the same theme bucket.",
                "Keep the watchlist short enough that each name can be reviewed manually.",
            ],
            "exclusions": [
                "Exclude low-score or unsupported-market candidates from the actionable watchlist.",
                "If cadence evidence is incomplete, keep the symbol in observation mode until more structure is available.",
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
            selected_as = self._selected_as(
                row,
                risk_level=risk_level,
                supported_market=supported_market,
                preferred_market=(not preferred or market in preferred),
            )
            if theme_counts[theme] >= 2 and selected_as == "beginner_watchlist":
                selected_as = "observe_only"
            if selected_as == "validate_only" and len(observations) >= max(2, max_items - 1):
                continue

            trading_level, trading_level_reasons = self._trading_level(row, risk_level=risk_level, supported_market=supported_market)
            data_sufficiency = self._data_sufficiency(row)
            validation_points = self._validation_points(
                row,
                risk_level=risk_level,
                supported_market=supported_market,
                trading_level=trading_level,
            )
            if trading_level == "needs_review":
                validation_points.append("Collect more structured evidence before assigning a concrete backtest cadence.")
                if selected_as == "beginner_watchlist":
                    selected_as = "observe_only"

            observation = CandidateObservation(
                symbol=str(row.get("symbol") or ""),
                market=market,
                selected_as=selected_as,
                score=round(self._rank_score(row), 2),
                reasons=self._reasons(row, theme, trading_level=trading_level),
                primary_risks=self._primary_risks(row, risk_level, supported_market, trading_level=trading_level),
                validation_points=list(dict.fromkeys(validation_points))[:7],
                meta={
                    "theme": theme,
                    "action_hint": row.get("action_hint"),
                    "framework_rules": self.framework_rules(),
                    "supported_market": supported_market,
                    "trading_level": trading_level,
                    "trading_level_reasons": trading_level_reasons,
                    "data_sufficiency": data_sufficiency,
                    "backtest_ready": trading_level in {"daily", "minute"},
                    "candidate_source": row.get("candidate_source"),
                    "strategy_tags": list(row.get("strategy_tags") or []),
                },
            )
            theme_counts[theme] += 1
            observations.append(observation)
        return observations

    def suggest_next_actions(self, observations: Iterable[CandidateObservation]) -> list[str]:
        actions: list[str] = []
        for item in observations:
            trading_level = str(item.meta.get("trading_level") or "needs_review")
            if item.selected_as == "beginner_watchlist":
                actions.append(f"Review {item.symbol} as a {trading_level} candidate and define one invalidation rule before backtesting.")
            elif item.selected_as == "observe_only":
                actions.append(f"Keep {item.symbol} in observation mode until the {trading_level} cadence evidence is stable.")
            else:
                actions.append(f"Use {item.symbol} as a validation-only example and do not promote it before cadence evidence improves.")
        return list(dict.fromkeys(actions))

    def summary(self, observations: Iterable[CandidateObservation]) -> dict[str, Any]:
        rows = list(observations)
        return {
            "counts": {
                "beginner_watchlist": sum(1 for row in rows if row.selected_as == "beginner_watchlist"),
                "observe_only": sum(1 for row in rows if row.selected_as == "observe_only"),
                "validate_only": sum(1 for row in rows if row.selected_as == "validate_only"),
            },
            "trading_level_counts": {
                "daily": sum(1 for row in rows if row.meta.get("trading_level") == "daily"),
                "minute": sum(1 for row in rows if row.meta.get("trading_level") == "minute"),
                "needs_review": sum(1 for row in rows if row.meta.get("trading_level") == "needs_review"),
            },
            "backtest_ready_count": sum(1 for row in rows if row.meta.get("backtest_ready")),
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
        text = " ".join(str(row.get(key) or "") for key in ("name", "rationale", "risk", "action_hint"))
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

    def _reasons(self, row: dict[str, Any], theme: str, *, trading_level: str) -> list[str]:
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
        reasons.append(f"Suggested trading level: {trading_level}")
        return list(dict.fromkeys(reasons))[:5]

    def _primary_risks(self, row: dict[str, Any], risk_level: str, supported_market: bool, *, trading_level: str) -> list[str]:
        risks: list[str] = []
        base_risk = str(row.get("risk") or "").strip()
        if base_risk:
            risks.append(base_risk)
        if risk_level == "high":
            risks.append("High volatility or narrative-driven price action can make execution and review harder for a beginner.")
        if not supported_market:
            risks.append("This market is not covered by the current beginner-safe automated execution path.")
        if trading_level == "minute":
            risks.append("Minute-level candidates need tighter execution and liquidity discipline than daily candidates.")
        if trading_level == "needs_review":
            risks.append("Current local evidence is not yet enough to justify a firm trading cadence.")
        return list(dict.fromkeys(risks))[:5]

    def _validation_points(
        self,
        row: dict[str, Any],
        risk_level: str,
        supported_market: bool,
        *,
        trading_level: str,
    ) -> list[str]:
        points = [
            "Confirm current liquidity and spread with a fresh quote snapshot before any stage upgrade.",
            "Write down one concrete invalidation condition for the name.",
        ]
        if trading_level == "daily":
            points.append("Use a daily-bar review schedule and confirm the thesis can survive overnight or multi-day holding.")
        elif trading_level == "minute":
            points.append("Validate minute-bar data coverage, turnover, and intraday execution assumptions before optimization.")
        else:
            points.append("Fill in missing cadence evidence such as liquidity comfort, signal freshness, or review frequency.")
        if risk_level != "low":
            points.append("Review whether recent volatility makes the name unsuitable for a beginner-sized position.")
        if not supported_market:
            points.append("Keep the name in research or validation mode until a matching local execution path exists.")
        return points

    def _trading_level(
        self,
        row: dict[str, Any],
        *,
        risk_level: str,
        supported_market: bool,
    ) -> tuple[str, list[str]]:
        text_parts = [
            str(row.get("name") or ""),
            str(row.get("rationale") or ""),
            str(row.get("risk") or ""),
            str(row.get("action_hint") or ""),
            " ".join(str(item) for item in (row.get("strategy_tags") or [])),
        ]
        text = " ".join(text_parts).lower()
        reasons: list[str] = []
        data_sufficiency = self._data_sufficiency(row)
        if not supported_market:
            return "needs_review", ["Unsupported market cannot be assigned a production-ready cadence in the current workflow."]
        if data_sufficiency < 0.45:
            return "needs_review", ["The candidate row is missing enough structured detail to determine daily or minute cadence."]
        if any(keyword in text for keyword in LOW_LIQUIDITY_KEYWORDS):
            return "needs_review", ["Liquidity comfort is weak, so cadence should stay pending until spreads and turnover are reviewed."]

        turnover = self._numeric((row.get("quote") or {}).get("turnover")) or self._numeric(row.get("turnover"))
        max_signal_score = self._numeric(row.get("max_signal_score"))
        signal_count = len([item for item in (row.get("signals") or []) if isinstance(item, dict)])

        minute_keyword_hit = any(keyword in text for keyword in MINUTE_LEVEL_KEYWORDS)
        daily_keyword_hit = any(keyword in text for keyword in DAILY_LEVEL_KEYWORDS)

        if minute_keyword_hit:
            reasons.append("The local thesis or action hint explicitly mentions intraday or minute-level handling.")
        if turnover is not None and turnover >= 50_000_000:
            reasons.append("Turnover appears large enough to support minute-level review if the data feed is clean.")
        if signal_count >= 2 or (max_signal_score is not None and max_signal_score >= 0.8):
            reasons.append("Signal freshness is strong enough to justify an intraday-style candidate review.")

        if minute_keyword_hit and risk_level != "high":
            return "minute", list(dict.fromkeys(reasons))[:3] or ["Minute-level language is present in the candidate input."]
        if turnover is not None and turnover >= 50_000_000 and signal_count >= 1 and risk_level == "low":
            return "minute", list(dict.fromkeys(reasons))[:3] or ["Liquidity and signal freshness support minute-level review."]

        daily_reasons: list[str] = []
        if daily_keyword_hit:
            daily_reasons.append("The local thesis reads like a low-frequency or multi-day setup.")
        if risk_level in {"low", "medium"}:
            daily_reasons.append("Execution risk looks manageable for a daily review routine.")
        daily_reasons.append("Daily cadence is the conservative default when intraday evidence is not explicit.")
        return "daily", list(dict.fromkeys(daily_reasons))[:3]

    def _data_sufficiency(self, row: dict[str, Any]) -> float:
        checks = [
            bool(str(row.get("symbol") or "").strip()),
            bool(str(row.get("market") or "").strip()),
            row.get("raw_score") not in {None, ""},
            bool(str(row.get("rationale") or "").strip()),
            bool(str(row.get("risk") or "").strip()),
            bool(row.get("signals")),
            bool(str(row.get("action_hint") or "").strip()),
        ]
        return round(sum(1 for item in checks if item) / len(checks), 2)

    def _numeric(self, value: Any) -> float | None:
        try:
            if value in {None, ""}:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
