from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import LlmTradingConfig, load_config
from .llm_client import LlmClientError, OpenAICompatibleClient

ALLOWED_STRATEGIES = {
    "trend_following",
    "breakout_momentum",
    "pullback_buy",
    "watch_only",
    "block_trade",
}


@dataclass(frozen=True)
class StrategyDecision:
    symbol: str
    strategy: str
    confidence: float
    allow_trade: bool
    reason: str
    risk_notes: list[str] = field(default_factory=list)
    source: str = "heuristic"
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_payload(cls, symbol: str, payload: dict[str, Any], source: str) -> "StrategyDecision":
        strategy = str(payload.get("strategy", "watch_only"))
        if strategy not in ALLOWED_STRATEGIES:
            strategy = "watch_only"
        confidence = max(0.0, min(float(payload.get("confidence", 0.0)), 1.0))
        allow_trade = bool(payload.get("allow_trade", strategy not in {"watch_only", "block_trade"}))
        if strategy in {"watch_only", "block_trade"}:
            allow_trade = False
        raw_risk_notes = payload.get("risk_notes", [])
        if isinstance(raw_risk_notes, str):
            risk_notes = [raw_risk_notes]
        else:
            risk_notes = [str(item) for item in raw_risk_notes]
        return cls(
            symbol=str(payload.get("symbol") or symbol),
            strategy=strategy,
            confidence=confidence,
            allow_trade=allow_trade,
            reason=str(payload.get("reason", "")),
            risk_notes=risk_notes,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "confidence": self.confidence,
            "allow_trade": self.allow_trade,
            "reason": self.reason,
            "risk_notes": self.risk_notes,
            "source": self.source,
            "decided_at": self.decided_at.isoformat(),
        }


class StrategySelector:
    def __init__(
        self,
        config: LlmTradingConfig | None = None,
        enable_llm: bool = False,
        enable_web_search: bool = False,
        min_confidence: float = 0.55,
        cache_minutes: int = 30,
    ) -> None:
        self.config = config
        self.enable_llm = enable_llm
        self.enable_web_search = enable_web_search
        self.min_confidence = min_confidence
        self.cache_ttl = timedelta(minutes=max(cache_minutes, 1))
        self.cache: dict[str, StrategyDecision] = {}

    @classmethod
    def from_config_path(
        cls,
        config_path: str = "",
        base_dir: str | None = None,
        enable_llm: bool = False,
        enable_web_search: bool = False,
        min_confidence: float = 0.55,
        cache_minutes: int = 30,
    ) -> "StrategySelector":
        config = load_config(config_path or None, base_dir) if enable_llm else None
        return cls(config, enable_llm, enable_web_search, min_confidence, cache_minutes)

    def select(self, features: dict[str, Any]) -> StrategyDecision:
        symbol = str(features.get("vt_symbol") or features.get("symbol") or "UNKNOWN")
        cached = self.cache.get(symbol)
        if cached and datetime.now(timezone.utc) - cached.decided_at <= self.cache_ttl:
            return cached

        decision = self._select_with_llm(features) if self.enable_llm else None
        if not decision or decision.confidence < self.min_confidence:
            decision = self._heuristic_select(features)
        self.cache[symbol] = decision
        return decision

    def _select_with_llm(self, features: dict[str, Any]) -> StrategyDecision | None:
        if not self.config:
            return None
        client = OpenAICompatibleClient(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            model=self.config.model,
            timeout_seconds=max(self.config.timeout_seconds, 45),
            max_retries=self.config.max_retries,
            api_type=self.config.llm_api_type,
            api_user=self.config.api_user,
            enable_web_search=self.enable_web_search or self.config.enable_web_search,
            temperature=self.config.temperature,
            conversation_id=self.config.conversation_id,
        )
        if not client.is_configured():
            return None

        system_prompt = """
你是量化交易系统的策略选择器，只负责选择策略，不允许直接下单或决定交易数量。
你只能输出严格 JSON，对象字段必须包含：symbol, strategy, confidence, allow_trade, reason, risk_notes。
strategy 只能从以下枚举选择：trend_following, breakout_momentum, pullback_buy, watch_only, block_trade。
选择规则：
- 趋势健康、均线多头、不过热、资金尚可：trend_following
- 放量接近/突破压力位、事件催化明确：breakout_momentum
- 长期趋势仍强但短线过热或回踩中：pullback_buy
- 信息不足、风险偏高或趋势冲突：watch_only
- 资金/风控/基本面明显恶化：block_trade
硬风控永远由系统执行，你不得建议绕过资金、亏损、交易次数限制。
""".strip()
        user_prompt = "输入特征：\n" + json.dumps(features, ensure_ascii=False)
        try:
            payload = client.complete_json(system_prompt, user_prompt)
        except LlmClientError:
            return None
        return StrategyDecision.from_payload(str(features.get("vt_symbol") or features.get("symbol") or "UNKNOWN"), payload, "llm")

    def _heuristic_select(self, features: dict[str, Any]) -> StrategyDecision:
        symbol = str(features.get("vt_symbol") or features.get("symbol") or "UNKNOWN")
        trend = float(features.get("trend_score", 0.0))
        risk = float(features.get("risk_score", 1.0))
        rsi = float(features.get("rsi14", 50.0) or 50.0)
        price = float(features.get("price", 0.0) or 0.0)
        resistance = float(features.get("resistance_20d", 0.0) or 0.0)
        return_20d = float(features.get("return_20d", 0.0) or 0.0)
        volume_ratio = float(features.get("volume_ratio", 1.0) or 1.0)
        capital_score = float(features.get("capital_score", 0.5) or 0.5)

        strategy = "watch_only"
        confidence = 0.55
        allow_trade = False
        reason = "默认观察：信号不足或风险偏高"
        risks: list[str] = []

        if risk >= 0.9:
            strategy = "block_trade"
            confidence = 0.7
            reason = "风险评分过高，阻止开仓"
            risks.append("risk_score_high")
        elif trend >= 0.75 and rsi < 72 and capital_score >= 0.45:
            strategy = "trend_following"
            confidence = 0.68
            allow_trade = True
            reason = "趋势较强且未严重超买，使用趋势跟踪"
        elif resistance and price >= resistance * 0.985 and volume_ratio >= 1.3 and rsi < 78:
            strategy = "breakout_momentum"
            confidence = 0.64
            allow_trade = True
            reason = "价格接近压力位且放量，使用突破动量"
        elif trend >= 0.75 and (rsi >= 72 or return_20d > 0.2):
            strategy = "pullback_buy"
            confidence = 0.62
            allow_trade = False
            reason = "长期趋势强但短线过热，只等待回踩"
            risks.append("overheated")
        elif capital_score < 0.35:
            strategy = "watch_only"
            confidence = 0.6
            reason = "资金承接偏弱，暂不交易"
            risks.append("weak_capital_flow")

        return StrategyDecision(
            symbol=symbol,
            strategy=strategy,
            confidence=confidence,
            allow_trade=allow_trade,
            reason=reason,
            risk_notes=risks,
            source="heuristic",
        )
