from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

VALID_STRATEGIES = {
    'trend_following',
    'breakout_momentum',
    'pullback_buy',
    'watch_only',
    'block_trade',
}


@dataclass
class ValidationResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: str = ''


def validate_json_only_response(text: str) -> ValidationResult:
    raw = (text or '').strip()
    if not raw:
        return ValidationResult(False, error='empty response')
    if raw.startswith('```') or raw.endswith('```'):
        return ValidationResult(False, error='markdown code block is forbidden')
    try:
        obj = json.loads(raw)
    except Exception as e:
        return ValidationResult(False, error=f'invalid json: {e}')
    if not isinstance(obj, dict):
        return ValidationResult(False, error='top-level json must be object')
    strategy = obj.get('strategy')
    if strategy not in VALID_STRATEGIES:
        return ValidationResult(False, error='invalid strategy enum')
    confidence = obj.get('confidence')
    if not isinstance(confidence, (int, float)):
        return ValidationResult(False, error='confidence must be number')
    reason = obj.get('reason')
    if not isinstance(reason, str):
        return ValidationResult(False, error='reason must be string')
    risk_flags = obj.get('risk_flags', [])
    if not isinstance(risk_flags, list):
        return ValidationResult(False, error='risk_flags must be list')
    return ValidationResult(True, data=obj)


def local_strategy_fallback(payload: dict[str, Any]) -> dict[str, Any]:
    risk_score = float(payload.get('risk_score', 0.5) or 0.5)
    capital_score = float(payload.get('capital_score', 0.5) or 0.5)
    trend_score = float(payload.get('trend_score', 0.5) or 0.5)
    rsi = float(payload.get('rsi', 50) or 50)
    catalyst = bool(payload.get('has_event_catalyst', False))
    near_resistance = bool(payload.get('near_resistance', False))
    moving_average_bullish = bool(payload.get('moving_average_bullish', False))
    missing_fields = int(payload.get('missing_fields', 0) or 0)

    if risk_score > 0.7 or capital_score < 0.3:
        strategy = 'block_trade'
        reason = '风险过高或资金条件不足，阻断交易。'
    elif missing_fields >= 3:
        strategy = 'watch_only'
        reason = '关键信息缺失较多，仅观察。'
    elif rsi > 80 and trend_score >= 0.8:
        strategy = 'pullback_buy'
        reason = '长期趋势强，但短线过热，等待回踩更优。'
    elif rsi > 80 and trend_score < 0.8:
        strategy = 'watch_only'
        reason = '短线过热且趋势支撑不足，先观察。'
    elif near_resistance and catalyst:
        strategy = 'breakout_momentum'
        reason = '接近压力位且有催化，适合突破跟随。'
    elif trend_score >= 0.7 and rsi < 70 and moving_average_bullish:
        strategy = 'trend_following'
        reason = '趋势健康且不过热，可顺势跟随。'
    else:
        strategy = 'watch_only'
        reason = '未满足明确开仓模式，先观察。'

    return {
        'strategy': strategy,
        'confidence': 0.78,
        'reason': reason,
        'risk_flags': [],
        'use_task_planning': False,
        'format_ok': True,
        'fallback_used': True,
    }
