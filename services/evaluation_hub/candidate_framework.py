from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .models import CandidateObservation


SUPPORTED_TRADING_MARKETS = {"us", "hong_kong"}
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
HARD_RISK_KEYWORDS = {
    "thin_liquidity": LOW_LIQUIDITY_KEYWORDS,
    "stale_data": ("stale", "outdated", "过期", "陈旧"),
    "execution_unavailable": ("execution unavailable", "cannot trade", "无法交易", "not tradable"),
}
SOFT_RISK_KEYWORDS = {
    "valuation_stretch": ("估值", "valuation", "valued", "valuation sensitivity"),
    "policy_uncertainty": ("政策", "regulation", "监管"),
    "high_volatility": ("高波动", "volatility", "高beta", "speculative"),
    "crowded_theme": ("拥挤", "theme", "热门"),
}
LEGACY_BUCKET_MAP = {
    "priority_trade": "beginner_watchlist",
    "active_watch": "observe_only",
    "research_queue": "validate_only",
    "exclude": "validate_only",
}


class TradingCandidateFramework:
    def framework_rules(self) -> dict[str, list[str]]:
        return {
            "stock_pool_scope": [
                "Rank candidates with trading-oriented thresholds first, then cap the displayed universe without changing the underlying bucket.",
                "Keep outputs directly reusable by backtest, readiness, and workflow evidence stages.",
            ],
            "liquidity_rules": [
                "Hard-block unsupported or thin-liquidity candidates from active trading buckets.",
                "Require stronger liquidity support for minute-level candidates than daily candidates.",
            ],
            "cadence_rules": [
                "Use daily cadence when the thesis supports multi-day review and no intraday-specific evidence is present.",
                "Use minute cadence only when turnover, signal freshness, and intraday language all support it.",
                "Keep cadence as needs_review when the local row is too incomplete for a reliable assignment.",
            ],
            "risk_rules": [
                "Separate hard risk flags from soft risk flags so unsupported execution and thin liquidity block promotion cleanly.",
                "Use risk_penalty as the primary quantitative penalty instead of explanation quality or teaching-oriented wording.",
            ],
            "bucket_rules": [
                "priority_trade requires high score, adequate liquidity, and bounded risk_penalty.",
                "active_watch keeps sub-threshold but still monitorable names visible without forcing them into a validation bucket.",
                "research_queue is for incomplete or lower-ranked names that still merit structured review.",
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
        grouped: dict[str, list[CandidateObservation]] = defaultdict(list)

        for row in ranked:
            market = str(row.get("market") or "")
            theme = self._infer_theme(row)
            supported_market = market in SUPPORTED_TRADING_MARKETS
            preferred_market = not preferred or market in preferred
            data_sufficiency = self._data_sufficiency(row)
            liquidity_score = self._liquidity_score(row)
            risk_penalty = self._risk_penalty(row)
            rank_score = round(self._rank_score(row), 2)
            trading_level, trading_level_reasons = self._trading_level(
                row,
                supported_market=supported_market,
                liquidity_score=liquidity_score,
                data_sufficiency=data_sufficiency,
            )
            hard_risk_flags = self._hard_risk_flags(
                row,
                supported_market=supported_market,
                preferred_market=preferred_market,
                data_sufficiency=data_sufficiency,
                liquidity_score=liquidity_score,
            )
            soft_risk_flags = self._soft_risk_flags(row)
            bucket, bucket_flags = self._assign_bucket(
                row,
                trading_level=trading_level,
                supported_market=supported_market,
                preferred_market=preferred_market,
                data_sufficiency=data_sufficiency,
                liquidity_score=liquidity_score,
                risk_penalty=risk_penalty,
            )
            hard_risk_flags = list(dict.fromkeys([*hard_risk_flags, *bucket_flags]))
            selected_as = self._legacy_selected_as(bucket)
            backtest_ready = trading_level in {"daily", "minute"} and bucket in {"priority_trade", "active_watch", "research_queue"}
            manual_review_required = bucket in {"research_queue", "exclude"} or trading_level == "needs_review"
            observation = CandidateObservation(
                symbol=str(row.get("symbol") or ""),
                market=market,
                selected_as=selected_as,
                score=rank_score,
                reasons=self._reasons(row, theme, trading_level=trading_level, bucket=bucket),
                primary_risks=self._primary_risks(
                    row,
                    hard_risk_flags=hard_risk_flags,
                    soft_risk_flags=soft_risk_flags,
                    trading_level=trading_level,
                ),
                validation_points=self._validation_points(
                    row,
                    trading_level=trading_level,
                    bucket=bucket,
                    hard_risk_flags=hard_risk_flags,
                ),
                bucket=bucket,
                trading_level=trading_level,
                hard_risk_flags=hard_risk_flags,
                soft_risk_flags=soft_risk_flags,
                research_confidence=self._research_confidence(row),
                liquidity_score=liquidity_score,
                risk_penalty=risk_penalty,
                raw_score=self._raw_score(row),
                rank_score=rank_score,
                backtest_ready=backtest_ready,
                manual_review_required=manual_review_required,
                meta={
                    "theme": theme,
                    "action_hint": row.get("action_hint"),
                    "framework_rules": self.framework_rules(),
                    "supported_market": supported_market,
                    "preferred_market": preferred_market,
                    "trading_level": trading_level,
                    "trading_level_reasons": trading_level_reasons,
                    "data_sufficiency": data_sufficiency,
                    "backtest_ready": backtest_ready,
                    "candidate_source": row.get("candidate_source"),
                    "strategy_tags": list(row.get("strategy_tags") or []),
                    "legacy_selected_as": selected_as,
                    "bucket": bucket,
                    "hard_risk_flags": hard_risk_flags,
                    "soft_risk_flags": soft_risk_flags,
                    "liquidity_score": liquidity_score,
                    "risk_penalty": risk_penalty,
                    "raw_score": self._raw_score(row),
                    "rank_score": rank_score,
                    "research_confidence": self._research_confidence(row),
                    "manual_review_required": manual_review_required,
                },
            )
            grouped[bucket].append(observation)

        ordered: list[CandidateObservation] = []
        display_caps = {
            "priority_trade": min(max_items, 5),
            "active_watch": min(max_items + 3, 8),
            "research_queue": min(max_items + 7, 12),
            "exclude": min(max_items, 3),
        }
        for bucket in ("priority_trade", "active_watch", "research_queue", "exclude"):
            ordered.extend(grouped[bucket][: display_caps[bucket]])
        return ordered[: max(max_items, len(grouped["priority_trade"]))] if ordered else []

    def suggest_next_actions(self, observations: Iterable[CandidateObservation]) -> list[str]:
        actions: list[str] = []
        for item in observations:
            trading_level = item.trading_level or str(item.meta.get("trading_level") or "needs_review")
            bucket = item.effective_bucket()
            if bucket == "priority_trade":
                actions.append(f"Review {item.symbol} as a {trading_level} priority trade candidate and confirm one invalidation rule before backtesting.")
            elif bucket == "active_watch":
                actions.append(f"Keep {item.symbol} in the active watch queue until the {trading_level} evidence is refreshed.")
            elif bucket == "research_queue":
                actions.append(f"Use {item.symbol} as a research-queue candidate until liquidity, cadence, or thesis evidence improves.")
            else:
                actions.append(f"Keep {item.symbol} excluded from active trading review until hard risk flags are cleared.")
        return list(dict.fromkeys(actions))

    def summary(self, observations: Iterable[CandidateObservation]) -> dict[str, Any]:
        rows = list(observations)
        return {
            "counts": {
                "priority_trade": sum(1 for row in rows if row.effective_bucket() == "priority_trade"),
                "active_watch": sum(1 for row in rows if row.effective_bucket() == "active_watch"),
                "research_queue": sum(1 for row in rows if row.effective_bucket() == "research_queue"),
                "exclude": sum(1 for row in rows if row.effective_bucket() == "exclude"),
            },
            "legacy_counts": {
                "beginner_watchlist": sum(1 for row in rows if row.selected_as == "beginner_watchlist"),
                "observe_only": sum(1 for row in rows if row.selected_as == "observe_only"),
                "validate_only": sum(1 for row in rows if row.selected_as == "validate_only"),
            },
            "trading_level_counts": {
                "daily": sum(1 for row in rows if (row.trading_level or row.meta.get("trading_level")) == "daily"),
                "minute": sum(1 for row in rows if (row.trading_level or row.meta.get("trading_level")) == "minute"),
                "needs_review": sum(1 for row in rows if (row.trading_level or row.meta.get("trading_level")) == "needs_review"),
            },
            "hard_block_count": sum(1 for row in rows if row.hard_risk_flags),
            "manual_review_required_count": sum(1 for row in rows if row.manual_review_required),
            "backtest_ready_count": sum(1 for row in rows if row.backtest_ready),
            "rules": self.framework_rules(),
        }

    def _assign_bucket(
        self,
        row: dict[str, Any],
        *,
        trading_level: str,
        supported_market: bool,
        preferred_market: bool,
        data_sufficiency: float,
        liquidity_score: float,
        risk_penalty: float,
    ) -> tuple[str, list[str]]:
        score = self._rank_score(row) / 100.0
        hard_flags: list[str] = []
        if not supported_market:
            return "exclude", ["unsupported_market"]
        if not preferred_market:
            return "research_queue", ["outside_preferred_market"]
        if data_sufficiency < 0.45:
            return "research_queue", ["insufficient_structured_data"]
        if liquidity_score < 0.35 or self._mentions_low_liquidity(row):
            return "exclude", ["thin_liquidity"]
        if trading_level == "needs_review":
            return "research_queue", ["cadence_needs_review"]

        if trading_level == "minute":
            if score >= 0.76 and liquidity_score >= 0.65 and risk_penalty <= 0.58:
                return "priority_trade", hard_flags
            if score >= 0.68 and liquidity_score >= 0.55:
                return "active_watch", hard_flags
            if score >= 0.60:
                return "research_queue", hard_flags
            return "exclude", hard_flags

        if score >= 0.78 and liquidity_score >= 0.55 and risk_penalty <= 0.65:
            return "priority_trade", hard_flags
        if score >= 0.70 and liquidity_score >= 0.45:
            return "active_watch", hard_flags
        if score >= 0.60:
            return "research_queue", hard_flags
        return "exclude", hard_flags

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

    def _reasons(self, row: dict[str, Any], theme: str, *, trading_level: str, bucket: str) -> list[str]:
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
        reasons.append(f"Trading cadence: {trading_level}")
        reasons.append(f"Candidate bucket: {bucket}")
        return list(dict.fromkeys(reasons))[:6]

    def _primary_risks(
        self,
        row: dict[str, Any],
        *,
        hard_risk_flags: list[str],
        soft_risk_flags: list[str],
        trading_level: str,
    ) -> list[str]:
        risks: list[str] = []
        base_risk = str(row.get("risk") or "").strip()
        if base_risk:
            risks.append(base_risk)
        if hard_risk_flags:
            risks.append("Hard risk flags are present and should block active promotion until resolved.")
        if soft_risk_flags:
            risks.append("Soft risk flags suggest tighter review, sizing, or backtest assumptions are needed.")
        if trading_level == "minute":
            risks.append("Minute-level candidates need tighter execution and liquidity discipline than daily candidates.")
        if trading_level == "needs_review":
            risks.append("Current local evidence is not yet enough to justify a firm trading cadence.")
        return list(dict.fromkeys(risks))[:5]

    def _validation_points(
        self,
        row: dict[str, Any],
        *,
        trading_level: str,
        bucket: str,
        hard_risk_flags: list[str],
    ) -> list[str]:
        points = [
            "Confirm current liquidity, spread, and turnover assumptions with a fresh quote snapshot before stage upgrade.",
            "Write down one concrete invalidation condition for the name.",
        ]
        if trading_level == "daily":
            points.append("Use a daily-bar review schedule and confirm the thesis can survive overnight or multi-day holding.")
        elif trading_level == "minute":
            points.append("Validate minute-bar data coverage, turnover, and intraday execution assumptions before optimization.")
        else:
            points.append("Fill in missing cadence evidence such as liquidity comfort, signal freshness, or review frequency.")
        if bucket == "research_queue":
            points.append("Keep the symbol in structured research review until trading cadence and risk assumptions are stable.")
        if bucket == "exclude":
            points.append("Do not include the symbol in active trade review until hard risk flags are cleared.")
        if hard_risk_flags:
            points.append("Resolve unsupported market, thin liquidity, or incomplete structured-data blockers before promotion.")
        return list(dict.fromkeys(points))[:7]

    def _trading_level(
        self,
        row: dict[str, Any],
        *,
        supported_market: bool,
        liquidity_score: float,
        data_sufficiency: float,
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
        if not supported_market:
            return "needs_review", ["Unsupported market cannot be assigned a production-ready cadence in the current workflow."]
        if data_sufficiency < 0.45:
            return "needs_review", ["The candidate row is missing enough structured detail to determine daily or minute cadence."]
        if liquidity_score < 0.35 or any(keyword in text for keyword in LOW_LIQUIDITY_KEYWORDS):
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
        if minute_keyword_hit and liquidity_score >= 0.65:
            return "minute", list(dict.fromkeys(reasons))[:3] or ["Minute-level language is present in the candidate input."]
        if turnover is not None and turnover >= 50_000_000 and signal_count >= 1 and liquidity_score >= 0.65:
            return "minute", list(dict.fromkeys(reasons))[:3] or ["Liquidity and signal freshness support minute-level review."]

        daily_reasons: list[str] = []
        if daily_keyword_hit:
            daily_reasons.append("The local thesis reads like a low-frequency or multi-day setup.")
        daily_reasons.append("Daily cadence is the default when intraday evidence is not explicit but the row is sufficiently complete.")
        return "daily", list(dict.fromkeys(daily_reasons))[:3]

    def _hard_risk_flags(
        self,
        row: dict[str, Any],
        *,
        supported_market: bool,
        preferred_market: bool,
        data_sufficiency: float,
        liquidity_score: float,
    ) -> list[str]:
        flags: list[str] = []
        if not supported_market:
            flags.append("unsupported_market")
        if not preferred_market:
            flags.append("outside_preferred_market")
        if data_sufficiency < 0.45:
            flags.append("insufficient_structured_data")
        if liquidity_score < 0.35 or self._mentions_low_liquidity(row):
            flags.append("thin_liquidity")
        haystack = self._risk_text(row)
        for flag, keywords in HARD_RISK_KEYWORDS.items():
            if any(keyword.lower() in haystack for keyword in keywords):
                flags.append(flag)
        return list(dict.fromkeys(flags))

    def _soft_risk_flags(self, row: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        haystack = self._risk_text(row)
        for flag, keywords in SOFT_RISK_KEYWORDS.items():
            if any(keyword.lower() in haystack for keyword in keywords):
                flags.append(flag)
        risk_level = self._risk_level(row)
        if risk_level == "high":
            flags.append("high_textual_risk")
        elif risk_level == "medium":
            flags.append("medium_textual_risk")
        return list(dict.fromkeys(flags))

    def _research_confidence(self, row: dict[str, Any]) -> float:
        notes = [
            bool(str(row.get("llm_reason") or "").strip()),
            bool(str(row.get("llm_summary") or "").strip()),
            bool(str(row.get("research_note") or "").strip()),
            bool(str(row.get("research_summary") or "").strip()),
            bool(str(row.get("analysis_summary") or "").strip()),
            bool(row.get("explanation_ready")),
        ]
        return round(sum(1 for item in notes if item) / len(notes), 2)

    def _liquidity_score(self, row: dict[str, Any]) -> float:
        explicit = self._numeric(row.get("liquidity_score"))
        if explicit is not None:
            return max(0.0, min(explicit, 1.0))
        turnover = self._numeric((row.get("quote") or {}).get("turnover")) or self._numeric(row.get("turnover"))
        if turnover is None:
            return 0.5 if not self._mentions_low_liquidity(row) else 0.25
        if turnover >= 200_000_000:
            return 0.85
        if turnover >= 100_000_000:
            return 0.72
        if turnover >= 50_000_000:
            return 0.62
        if turnover >= 20_000_000:
            return 0.50
        if turnover >= 5_000_000:
            return 0.38
        return 0.24

    def _risk_penalty(self, row: dict[str, Any]) -> float:
        explicit = self._numeric(row.get("risk_penalty"))
        if explicit is not None:
            return max(0.0, min(explicit, 1.0))
        risk_level = self._risk_level(row)
        if risk_level == "high":
            return 0.72
        if risk_level == "medium":
            return 0.48
        return 0.24

    def _raw_score(self, row: dict[str, Any]) -> float | None:
        raw_score = self._numeric(row.get("raw_score"))
        if raw_score is None:
            return None
        return max(0.0, min(raw_score, 1.0))

    def _rank_score(self, row: dict[str, Any]) -> float:
        raw_score = float(self._raw_score(row) or 0.0) * 100.0
        signals = row.get("signals") or []
        signal_bonus = max((float(item.get("score") or 0.0) for item in signals if isinstance(item, dict)), default=0.0) * 10.0
        return raw_score + signal_bonus

    def _legacy_selected_as(self, bucket: str) -> str:
        return LEGACY_BUCKET_MAP.get(bucket, "validate_only")

    def _risk_text(self, row: dict[str, Any]) -> str:
        return " ".join(str(row.get(key) or "") for key in ("risk", "rationale", "action_hint", "liquidity_note")).lower()

    def _mentions_low_liquidity(self, row: dict[str, Any]) -> bool:
        haystack = self._risk_text(row)
        return any(keyword.lower() in haystack for keyword in LOW_LIQUIDITY_KEYWORDS)

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


BeginnerCandidateFramework = TradingCandidateFramework
