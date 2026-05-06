from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from futu import KLType, OpenQuoteContext, RET_OK, SubType, logger as futu_logger

futu_logger._console_level = 50
futu_logger.console_logger.setLevel(50)
futu_logger.consoleHandler.setLevel(50)

from vnpy_llm.base import format_datetime, utc_now
from vnpy_llm.config import LlmTradingConfig, load_config
from vnpy_llm.llm_client import LlmClientError, OpenAICompatibleClient
from vnpy_llm.news_feed import load_news_files
from vnpy_llm.rag_store import LocalRagStore

VT_TO_FUTU_EXCHANGE = {
    "SMART": "US",
    "NYSE": "US",
    "NASDAQ": "US",
    "AMEX": "US",
    "SEHK": "HK",
    "SSE": "SH",
    "SZSE": "SZ",
}
FUTU_TO_VT_EXCHANGE = {
    "US": "SMART",
    "HK": "SEHK",
    "SH": "SSE",
    "SZ": "SZSE",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="单次调用综合分析当前股票并输出操作参考 JSON")
    parser.add_argument("symbol", help="股票代码，如 AAPL、US.AAPL、AAPL.SMART、HK.00700、00700.SEHK")
    parser.add_argument("--market", default="US", choices=["US", "HK", "SH", "SZ"], help="未带市场前缀时使用的市场")
    parser.add_argument("--host", default="127.0.0.1", help="OpenD 地址")
    parser.add_argument("--port", type=int, default=11111, help="OpenD 端口")
    parser.add_argument("--days", type=int, default=365, help="历史 K 线回看自然日")
    parser.add_argument("--capital", type=float, default=10_000, help="用于仓位建议的参考资金")
    parser.add_argument("--config", default="", help="LLM 配置文件，默认读取 .vntrader/llm_trading_setting.json；不存在时尝试示例配置")
    parser.add_argument("--enable-web-search", action="store_true", help="允许 LLM/Agent 联网补充新闻、评论、财报和投行预测")
    parser.add_argument("--no-llm", action="store_true", help="只使用 Futu OpenAPI 和本地量化规则，不调用 LLM")
    parser.add_argument("--output", default="", help="可选：输出 JSON 文件路径")
    return parser


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def clean_value(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if hasattr(value, "item"):
        return clean_value(value.item())
    if isinstance(value, dict):
        return {str(k): clean_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_value(v) for v in value]
    return value


def normalize_symbol(symbol: str, market: str) -> tuple[str, str, str]:
    raw = symbol.strip().upper()
    if "." in raw:
        left, right = raw.split(".", 1)
        if left in FUTU_TO_VT_EXCHANGE:
            futu_code = raw
            vt_symbol = f"{right}.{FUTU_TO_VT_EXCHANGE[left]}"
            return futu_code, vt_symbol, left
        if right in VT_TO_FUTU_EXCHANGE:
            futu_market = VT_TO_FUTU_EXCHANGE[right]
            return f"{futu_market}.{left}", raw, futu_market
        raise ValueError(f"无法识别代码格式：{symbol}")

    futu_market = market.upper()
    code = raw
    if futu_market == "HK" and raw.isdigit():
        code = raw.zfill(5)
    vt_exchange = FUTU_TO_VT_EXCHANGE[futu_market]
    return f"{futu_market}.{code}", f"{code}.{vt_exchange}", futu_market


def dataframe_records(data: Any, limit: int = 5) -> list[dict[str, Any]]:
    if not hasattr(data, "tail"):
        return []
    return clean_value(data.tail(limit).to_dict("records"))


def fetch_history(ctx: OpenQuoteContext, futu_code: str, days: int) -> list[dict[str, Any]]:
    end = date.today()
    start = end - timedelta(days=days)
    ret, data, _ = ctx.request_history_kline(
        futu_code,
        start=str(start),
        end=str(end),
        ktype=KLType.K_DAY,
        max_count=max(260, min(days, 1000)),
    )
    if ret != RET_OK:
        raise RuntimeError(f"查询历史K线失败：{data}")
    return clean_value(data.to_dict("records"))


def sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return mean(values[-window:])


def pct_change(values: list[float], window: int) -> float | None:
    if len(values) <= window or values[-window - 1] == 0:
        return None
    return values[-1] / values[-window - 1] - 1


def calc_rsi(closes: list[float], window: int = 14) -> float | None:
    if len(closes) <= window:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(len(closes) - window, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def calc_atr(rows: list[dict[str, Any]], window: int = 14) -> float | None:
    if len(rows) <= window:
        return None
    true_ranges: list[float] = []
    for i in range(len(rows) - window, len(rows)):
        high = safe_float(rows[i].get("high"))
        low = safe_float(rows[i].get("low"))
        prev_close = safe_float(rows[i - 1].get("close"))
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return mean(true_ranges)


def max_drawdown(closes: list[float], window: int = 120) -> float:
    sample = closes[-window:]
    if not sample:
        return 0.0
    peak = sample[0]
    drawdown = 0.0
    for price in sample:
        peak = max(peak, price)
        if peak:
            drawdown = min(drawdown, price / peak - 1)
    return abs(drawdown)


def technical_analysis(history: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, Any]:
    closes = [safe_float(row.get("close")) for row in history if safe_float(row.get("close")) > 0]
    highs = [safe_float(row.get("high")) for row in history if safe_float(row.get("high")) > 0]
    lows = [safe_float(row.get("low")) for row in history if safe_float(row.get("low")) > 0]
    volumes = [safe_float(row.get("volume")) for row in history if safe_float(row.get("volume")) >= 0]
    last = safe_float(snapshot.get("last_price"), closes[-1] if closes else 0)
    current_volume = safe_float(snapshot.get("volume"), volumes[-1] if volumes else 0)
    avg_volume_20 = mean(volumes[-20:]) if len(volumes) >= 20 else 0
    volume_ratio = safe_float(snapshot.get("volume_ratio"), current_volume / avg_volume_20 if avg_volume_20 else 0)
    sma20 = sma(closes, 20)
    sma60 = sma(closes, 60)
    sma120 = sma(closes, 120)
    atr14 = calc_atr(history, 14)
    rsi14 = calc_rsi(closes, 14)
    daily_returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    vol20 = pstdev(daily_returns[-20:]) * math.sqrt(252) if len(daily_returns) >= 20 else None
    high20 = max(highs[-20:]) if len(highs) >= 20 else None
    low20 = min(lows[-20:]) if len(lows) >= 20 else None

    trend_points = 0
    trend_points += 1 if sma20 and last > sma20 else 0
    trend_points += 1 if sma20 and sma60 and sma20 > sma60 else 0
    trend_points += 1 if sma60 and sma120 and sma60 > sma120 else 0
    trend_points += 1 if (pct_change(closes, 20) or 0) > 0 else 0
    trend_score = trend_points / 4

    risk_score = 0.25
    if vol20 is not None:
        risk_score += min(vol20 / 1.2, 0.35)
    risk_score += min(max_drawdown(closes, 120), 0.25)
    if rsi14 and (rsi14 > 75 or rsi14 < 25):
        risk_score += 0.1
    if volume_ratio > 2 and last < safe_float(snapshot.get("prev_close_price"), last):
        risk_score += 0.1
    risk_score = min(risk_score, 1.0)

    return clean_value(
        {
            "last_price": last,
            "change_pct": last / safe_float(snapshot.get("prev_close_price"), last) - 1 if safe_float(snapshot.get("prev_close_price"), 0) else None,
            "volume": current_volume,
            "avg_volume_20": avg_volume_20,
            "volume_ratio": volume_ratio,
            "sma20": sma20,
            "sma60": sma60,
            "sma120": sma120,
            "return_5d": pct_change(closes, 5),
            "return_20d": pct_change(closes, 20),
            "return_60d": pct_change(closes, 60),
            "rsi14": rsi14,
            "atr14": atr14,
            "atr_pct": atr14 / last if atr14 and last else None,
            "annualized_volatility_20d": vol20,
            "max_drawdown_120d": max_drawdown(closes, 120),
            "support_20d": low20,
            "resistance_20d": high20,
            "trend_score": trend_score,
            "risk_score": risk_score,
        }
    )


def summarize_capital(ctx: OpenQuoteContext, futu_code: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    ret, data = ctx.get_capital_flow(futu_code)
    if ret == RET_OK and hasattr(data, "tail") and len(data):
        tail = data.tail(20)
        latest = tail.iloc[-1].to_dict()
        result["capital_flow_latest"] = clean_value(latest)
        result["capital_flow_20_sum"] = clean_value(
            {
                "in_flow": safe_float(tail["in_flow"].sum()) if "in_flow" in tail else 0,
                "super_in_flow": safe_float(tail["super_in_flow"].sum()) if "super_in_flow" in tail else 0,
                "big_in_flow": safe_float(tail["big_in_flow"].sum()) if "big_in_flow" in tail else 0,
            }
        )
    else:
        result["capital_flow_error"] = str(data)

    ret, data = ctx.get_capital_distribution(futu_code)
    if ret == RET_OK and hasattr(data, "iloc") and len(data):
        row = data.iloc[-1].to_dict()
        inflow = sum(safe_float(row.get(key)) for key in ["capital_in_super", "capital_in_big", "capital_in_mid", "capital_in_small"])
        outflow = sum(safe_float(row.get(key)) for key in ["capital_out_super", "capital_out_big", "capital_out_mid", "capital_out_small"])
        result["capital_distribution"] = clean_value({**row, "net_inflow": inflow - outflow})
    else:
        result["capital_distribution_error"] = str(data)
    return result


def fetch_financial_unusual(ctx: OpenQuoteContext, futu_code: str) -> dict[str, Any]:
    ret, data = ctx.get_financial_unusual(futu_code)
    if ret != RET_OK:
        return {"error": str(data)}
    return clean_value(data)


def strategy_votes(snapshot: dict[str, Any], technical: dict[str, Any], capital: dict[str, Any]) -> list[dict[str, Any]]:
    last = safe_float(technical.get("last_price"))
    sma20 = safe_float(technical.get("sma20"))
    sma60 = safe_float(technical.get("sma60"))
    rsi14 = safe_float(technical.get("rsi14"), 50)
    volume_ratio = safe_float(technical.get("volume_ratio"), 1)
    return20 = safe_float(technical.get("return_20d"))
    atr_pct = safe_float(technical.get("atr_pct"))
    resistance = safe_float(technical.get("resistance_20d"))
    support = safe_float(technical.get("support_20d"))
    net_inflow = safe_float(capital.get("capital_distribution", {}).get("net_inflow"))
    votes: list[dict[str, Any]] = []

    if last > sma20 > sma60 and return20 > 0:
        votes.append({"strategy": "CTA趋势跟踪", "vote": "bullish", "reason": "价格位于20/60日均线之上且20日收益为正"})
    elif last < sma20 or sma20 < sma60:
        votes.append({"strategy": "CTA趋势跟踪", "vote": "bearish", "reason": "价格跌破短均线或短均线弱于中均线"})
    else:
        votes.append({"strategy": "CTA趋势跟踪", "vote": "neutral", "reason": "趋势条件不完整"})

    if rsi14 < 30:
        votes.append({"strategy": "RSI均值回归", "vote": "bullish", "reason": "RSI低于30，存在超卖修复可能"})
    elif rsi14 > 70:
        votes.append({"strategy": "RSI均值回归", "vote": "bearish", "reason": "RSI高于70，短线过热"})
    else:
        votes.append({"strategy": "RSI均值回归", "vote": "neutral", "reason": "RSI处于中性区间"})

    if resistance and last >= resistance * 0.995 and volume_ratio >= 1.2:
        votes.append({"strategy": "放量突破", "vote": "bullish", "reason": "接近20日高点且量比高于1.2"})
    elif support and last <= support * 1.005:
        votes.append({"strategy": "区间破位", "vote": "bearish", "reason": "接近20日低点"})
    else:
        votes.append({"strategy": "突破/支撑", "vote": "neutral", "reason": "未形成明确突破或破位"})

    if net_inflow > 0:
        votes.append({"strategy": "资金流确认", "vote": "bullish", "reason": "大单/总资金净流入为正"})
    elif net_inflow < 0:
        votes.append({"strategy": "资金流确认", "vote": "bearish", "reason": "资金净流出"})
    else:
        votes.append({"strategy": "资金流确认", "vote": "neutral", "reason": "资金流方向不明确"})

    if atr_pct > 0.05:
        votes.append({"strategy": "ATR风控", "vote": "bearish", "reason": "ATR占价格比例较高，需降低仓位"})
    else:
        votes.append({"strategy": "ATR风控", "vote": "neutral", "reason": "波动处于可控区间"})

    pe_ttm = safe_float(snapshot.get("pe_ttm_ratio"))
    eps = safe_float(snapshot.get("earning_per_share"))
    if eps > 0 and 0 < pe_ttm < 35:
        votes.append({"strategy": "估值质量", "vote": "bullish", "reason": "EPS为正且PE_TTM未显著偏高"})
    elif pe_ttm >= 60 or eps <= 0:
        votes.append({"strategy": "估值质量", "vote": "bearish", "reason": "估值偏高或盈利为负"})
    else:
        votes.append({"strategy": "估值质量", "vote": "neutral", "reason": "估值信号中性"})

    return votes


def heuristic_recommendation(technical: dict[str, Any], votes: list[dict[str, Any]]) -> dict[str, Any]:
    bullish = sum(1 for item in votes if item["vote"] == "bullish")
    bearish = sum(1 for item in votes if item["vote"] == "bearish")
    trend_score = safe_float(technical.get("trend_score"))
    risk_score = safe_float(technical.get("risk_score"), 1)
    last = safe_float(technical.get("last_price"))
    atr = safe_float(technical.get("atr14"))
    support = safe_float(technical.get("support_20d"))
    resistance = safe_float(technical.get("resistance_20d"))
    raw_score = 0.45 + 0.12 * bullish - 0.14 * bearish + 0.25 * (trend_score - 0.5) - 0.25 * (risk_score - 0.5)
    score = max(0.0, min(raw_score, 1.0))

    if score >= 0.68 and risk_score < 0.7:
        action = "allow_long"
        advice = "可小仓试多或持有，等待回撤到支撑/均线附近再加仓"
    elif score >= 0.52:
        action = "watch_only"
        advice = "观望为主，只保留已有仓位或等待趋势/资金流进一步确认"
    elif score >= 0.38:
        action = "reduce_only"
        advice = "不建议新增多头，已有仓位考虑降低敞口"
    else:
        action = "avoid_new_long"
        advice = "规避新增多头，等待风险释放"

    stop_loss = None
    take_profit = None
    if last > 0:
        stop_candidates = [last * 0.95]
        if atr:
            stop_candidates.append(last - 2 * atr)
        if support:
            stop_candidates.append(support * 0.99)
        stop_loss = max(price for price in stop_candidates if price > 0)
        take_candidates = [last * 1.08]
        if atr:
            take_candidates.append(last + 3 * atr)
        if resistance and resistance > last:
            take_candidates.append(resistance)
        take_profit = min(price for price in take_candidates if price > last) if any(price > last for price in take_candidates) else None

    return clean_value(
        {
            "action": action,
            "advice": advice,
            "score": score,
            "confidence": min(0.85, 0.45 + 0.06 * len(votes)),
            "position_multiplier": max(0.0, min(score * (1 - risk_score * 0.5), 1.0)),
            "reference_stop_loss": stop_loss,
            "reference_take_profit": take_profit,
            "risk_note": "仅作量化研究和模拟交易参考，不构成个性化投资建议；真实交易需自行确认风险承受能力。",
        }
    )


def load_effective_config(args: argparse.Namespace) -> LlmTradingConfig:
    if args.config:
        return load_config(args.config, PROJECT_ROOT)
    default_path = PROJECT_ROOT.joinpath(".vntrader", "llm_trading_setting.json")
    example_path = PROJECT_ROOT.joinpath("examples", "futu_trader", "config", "llm_trading_setting.example.json")
    if not default_path.exists() and example_path.exists() and os.environ.get("KNOT_API_TOKEN"):
        return load_config(example_path, PROJECT_ROOT)
    return load_config(None, PROJECT_ROOT)


def local_evidence(config: LlmTradingConfig, symbol: str, name: str, max_items: int = 12) -> list[dict[str, Any]]:
    if not config.news_paths:
        return []
    items = load_news_files(config.news_paths)
    if not items:
        return []
    terms = [symbol, name, "earnings", "analyst", "rating", "forecast", "risk", "sentiment"]
    evidence = LocalRagStore(items).search(terms, utc_now(), max_items=max_items)
    return [item.to_dict() for item in evidence]


def build_llm_payload(
    futu_code: str,
    vt_symbol: str,
    snapshot: dict[str, Any],
    history_tail: list[dict[str, Any]],
    technical: dict[str, Any],
    capital: dict[str, Any],
    financial_unusual: dict[str, Any],
    votes: list[dict[str, Any]],
    recommendation: dict[str, Any],
    evidence: list[dict[str, Any]],
    capital_base: float,
) -> dict[str, Any]:
    return {
        "task": "综合当前股票的行情、量比、财务快照、财报/异常、投行预测、走势、资金流、评论/新闻情绪、风险和经典量化策略，输出当前操作参考。",
        "constraints": [
            "只能基于输入数据和联网检索可验证信息，不得编造财报、投行评级或目标价。",
            "不得直接下单；输出为研究建议和模拟交易参考。",
            "如财报、投行预测或评论情绪缺失，请明确标注 unknown，并降低 confidence。",
        ],
        "expected_sources_or_skills": [
            "Futu OpenAPI: 当前价、量比、资金流、财务快照、K线",
            "News Search / Stock News Digest: 新闻、公告、财报事件",
            "Comment Sentiment: 社区评论多空情绪",
            "Agent/Web Search: 投行评级、目标价、风险事件交叉验证",
            "Classic Quant: CTA趋势、RSI均值回归、突破、ATR风控、资金流确认、估值质量",
        ],
        "futu_code": futu_code,
        "vt_symbol": vt_symbol,
        "as_of": format_datetime(utc_now()),
        "capital_base": capital_base,
        "snapshot": snapshot,
        "history_tail": history_tail,
        "technical": technical,
        "capital_flow": capital,
        "financial_unusual": financial_unusual,
        "quant_strategy_votes": votes,
        "heuristic_recommendation": recommendation,
        "local_evidence": evidence,
    }


def llm_refine(config: LlmTradingConfig, payload: dict[str, Any], enable_web_search: bool) -> dict[str, Any] | None:
    client = OpenAICompatibleClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        timeout_seconds=max(config.timeout_seconds, 60),
        max_retries=config.max_retries,
        api_type=config.llm_api_type,
        api_user=config.api_user,
        enable_web_search=enable_web_search or config.enable_web_search,
        temperature=config.temperature,
        conversation_id=config.conversation_id,
    )
    if not client.is_configured():
        return None

    system_prompt = """
你是 StockOneShotAdvisorAgent，服务于量化交易系统。你需要把 Futu OpenAPI 数据、新闻/公告、评论情绪、财报与投行预测、资金流和经典量化策略融合成一个 JSON 研究结论。
要求：
1. 只输出严格 JSON 对象，不要 Markdown。
2. 不得编造事实；财报、目标价、评级、评论情绪无法验证时写 unknown。
3. 不得输出下单指令，只能输出 allow_long/watch_only/reduce_only/avoid_new_long 之一作为研究参考。
4. recommendation 必须包含 action、advice、confidence、position_multiplier、reference_stop_loss、reference_take_profit、main_reasons、main_risks。
5. position_multiplier 范围 0-1；高风险或证据不足时降低仓位和置信度。
""".strip()
    user_prompt = "输入 JSON：\n" + json.dumps(payload, ensure_ascii=False)
    return client.complete_json(system_prompt, user_prompt)


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    futu_code, vt_symbol, market = normalize_symbol(args.symbol, args.market)
    ctx = OpenQuoteContext(args.host, args.port)
    try:
        ret, snapshot_data = ctx.get_market_snapshot([futu_code])
        if ret != RET_OK or not len(snapshot_data):
            raise RuntimeError(f"查询快照失败：{snapshot_data}")
        snapshot = clean_value(snapshot_data.iloc[0].to_dict())

        ctx.subscribe([futu_code], [SubType.QUOTE], subscribe_push=False)
        history = fetch_history(ctx, futu_code, args.days)
        technical = technical_analysis(history, snapshot)
        capital = summarize_capital(ctx, futu_code)
        financial_unusual = fetch_financial_unusual(ctx, futu_code)
        votes = strategy_votes(snapshot, technical, capital)
        recommendation = heuristic_recommendation(technical, votes)
    finally:
        ctx.close()

    config = load_effective_config(args)
    evidence = local_evidence(config, futu_code.split(".", 1)[1], str(snapshot.get("name") or ""))
    payload = build_llm_payload(
        futu_code,
        vt_symbol,
        snapshot,
        history[-5:],
        technical,
        capital,
        financial_unusual,
        votes,
        recommendation,
        evidence,
        args.capital,
    )

    llm_result = None
    llm_error = ""
    if not args.no_llm:
        try:
            llm_result = llm_refine(config, payload, args.enable_web_search)
        except LlmClientError as exc:
            llm_error = str(exc)

    result = {
        "agent": "StockOneShotAdvisorAgent",
        "futu_code": futu_code,
        "vt_symbol": vt_symbol,
        "market": market,
        "as_of": format_datetime(utc_now()),
        "data": payload,
        "recommendation": llm_result.get("recommendation", llm_result) if isinstance(llm_result, dict) else recommendation,
        "llm_result": llm_result,
        "data_quality": {
            "opend": "ok",
            "history_rows": len(history),
            "local_evidence_count": len(evidence),
            "llm_used": bool(llm_result),
            "llm_error": llm_error,
            "web_search_enabled": bool(args.enable_web_search or config.enable_web_search),
        },
    }
    return clean_value(result)


def main() -> None:
    args = build_parser().parse_args()
    result = analyze(args)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT.joinpath(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
