from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from futu import KLType, OpenQuoteContext, RET_OK, logger as futu_logger

futu_logger._console_level = 50
futu_logger.console_logger.setLevel(50)
futu_logger.consoleHandler.setLevel(50)

from vnpy_llm.config import load_config
from vnpy_llm.llm_client import LlmClientError, OpenAICompatibleClient


VT_TO_FUTU_EXCHANGE = {"SEHK": "HK", "SSE": "SH", "SZSE": "SZ", "SMART": "US"}
FUTU_TO_VT_EXCHANGE = {"HK": "SEHK", "SH": "SSE", "SZ": "SZSE", "US": "SMART"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按市场构建独立量化观察池（HK/CN/US），不影响现有美股流程")
    parser.add_argument("--universe", required=True, help="候选池 JSON 文件")
    parser.add_argument("--market", default="", choices=["", "HK", "CN", "US"], help="覆盖候选池市场")
    parser.add_argument("--top-n", type=int, default=8)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--config", default="")
    parser.add_argument("--enable-agent", action="store_true", help="调用 KNOT/LLM 对本地量化排序做二次评估")
    parser.add_argument("--enable-web-search", action="store_true")
    parser.add_argument("--output", required=True)
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


def normalize_candidate(row: dict[str, Any], market: str) -> dict[str, Any]:
    symbol = str(row.get("symbol") or row.get("code") or "").strip().upper()
    vt_symbol = str(row.get("vt_symbol") or "").strip().upper()
    if vt_symbol and "." in vt_symbol:
        code, vt_exchange = vt_symbol.split(".", 1)
        futu_exchange = VT_TO_FUTU_EXCHANGE[vt_exchange]
        futu_code = f"{futu_exchange}.{code}"
        symbol = code
    else:
        if market == "HK":
            code = symbol.zfill(5) if symbol.isdigit() else symbol
            futu_code = f"HK.{code}"
            vt_symbol = f"{code}.SEHK"
        elif market == "CN":
            exchange = "SH" if symbol.startswith(("5", "6", "9")) else "SZ"
            vt_exchange = FUTU_TO_VT_EXCHANGE[exchange]
            futu_code = f"{exchange}.{symbol}"
            vt_symbol = f"{symbol}.{vt_exchange}"
        else:
            futu_code = f"US.{symbol}"
            vt_symbol = f"{symbol}.SMART"
    return {**row, "symbol": symbol, "vt_symbol": vt_symbol, "futu_code": futu_code}


def load_universe(path: Path, market_override: str) -> tuple[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    market = market_override or str(data.get("market") or "US").upper()
    rows = data.get("candidates", data.get("items", []))
    return market, [normalize_candidate(row, market) for row in rows]


def fetch_history(ctx: OpenQuoteContext, code: str, days: int) -> list[dict[str, Any]]:
    end = date.today()
    start = end - timedelta(days=days)
    ret, data, _ = ctx.request_history_kline(
        code,
        start=str(start),
        end=str(end),
        ktype=KLType.K_DAY,
        max_count=max(260, min(days, 1000)),
    )
    if ret != RET_OK:
        return []
    return clean_value(data.to_dict("records"))


def sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return mean(values[-window:])


def pct_change(values: list[float], window: int) -> float | None:
    if len(values) <= window or not values[-window - 1]:
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
    return 100 - 100 / (1 + avg_gain / avg_loss)


def calc_atr_pct(rows: list[dict[str, Any]], price: float, window: int = 14) -> float | None:
    if len(rows) <= window or price <= 0:
        return None
    trs: list[float] = []
    for i in range(len(rows) - window, len(rows)):
        high = safe_float(rows[i].get("high"))
        low = safe_float(rows[i].get("low"))
        prev_close = safe_float(rows[i - 1].get("close"))
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return mean(trs) / price


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


def technical_score(history: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, Any]:
    closes = [safe_float(row.get("close")) for row in history if safe_float(row.get("close")) > 0]
    vols = [safe_float(row.get("volume")) for row in history if safe_float(row.get("volume")) >= 0]
    price = safe_float(snapshot.get("last_price"), closes[-1] if closes else 0)
    sma20 = sma(closes, 20)
    sma60 = sma(closes, 60)
    sma120 = sma(closes, 120)
    ret20 = pct_change(closes, 20)
    rsi14 = calc_rsi(closes)
    atr_pct = calc_atr_pct(history, price)
    volume_ratio = safe_float(snapshot.get("volume_ratio"), safe_float(snapshot.get("volume")) / mean(vols[-20:]) if len(vols) >= 20 and mean(vols[-20:]) else 0)

    trend_points = 0
    trend_points += 1 if sma20 and price > sma20 else 0
    trend_points += 1 if sma20 and sma60 and sma20 > sma60 else 0
    trend_points += 1 if sma60 and sma120 and sma60 > sma120 else 0
    trend_points += 1 if (ret20 or 0) > 0 else 0
    trend = trend_points / 4

    risk = 0.25 + min(max_drawdown(closes), 0.3)
    if atr_pct:
        risk += min(atr_pct * 4, 0.3)
    if rsi14 and (rsi14 > 75 or rsi14 < 25):
        risk += 0.1
    risk = min(risk, 1.0)

    return clean_value({
        "last_price": price,
        "volume_ratio": volume_ratio,
        "sma20": sma20,
        "sma60": sma60,
        "return_20d": ret20,
        "rsi14": rsi14,
        "atr_pct": atr_pct,
        "max_drawdown_120d": max_drawdown(closes),
        "trend_score": trend,
        "risk_score": risk,
    })


def capital_score(ctx: OpenQuoteContext, code: str) -> dict[str, Any]:
    ret, data = ctx.get_capital_distribution(code)
    if ret != RET_OK or not hasattr(data, "iloc") or not len(data):
        return {"net_inflow": None, "capital_score": 0.5, "error": str(data)}
    row = data.iloc[-1].to_dict()
    inflow = sum(safe_float(row.get(k)) for k in ["capital_in_super", "capital_in_big", "capital_in_mid", "capital_in_small"])
    outflow = sum(safe_float(row.get(k)) for k in ["capital_out_super", "capital_out_big", "capital_out_mid", "capital_out_small"])
    net = inflow - outflow
    score = 0.65 if net > 0 else 0.35 if net < 0 else 0.5
    return clean_value({"net_inflow": net, "capital_score": score, "update_time": row.get("update_time")})


def valuation_score(snapshot: dict[str, Any], asset_type: str) -> float:
    if asset_type.upper() == "ETF":
        return 0.55
    pe = safe_float(snapshot.get("pe_ttm_ratio"))
    eps = safe_float(snapshot.get("earning_per_share"))
    if eps <= 0 or pe <= 0:
        return 0.25
    if pe < 15:
        return 0.75
    if pe < 35:
        return 0.65
    if pe < 60:
        return 0.45
    return 0.25


def local_rank(ctx: OpenQuoteContext, candidates: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    ret, snapshot_df = ctx.get_market_snapshot([item["futu_code"] for item in candidates])
    snapshots = {}
    if ret == RET_OK:
        for _, row in snapshot_df.iterrows():
            snapshots[str(row["code"])] = clean_value(row.to_dict())

    ranked: list[dict[str, Any]] = []
    for item in candidates:
        snapshot = snapshots.get(item["futu_code"], {})
        history = fetch_history(ctx, item["futu_code"], days)
        tech = technical_score(history, snapshot)
        capital = capital_score(ctx, item["futu_code"])
        liquidity = min(max(math.log10(max(safe_float(snapshot.get("turnover")), 1)) / 11, 0), 1)
        val_score = valuation_score(snapshot, str(item.get("asset_type", "Stock")))
        risk = safe_float(tech.get("risk_score"), 1)
        final = (
            safe_float(tech.get("trend_score")) * 0.35
            + liquidity * 0.15
            + safe_float(capital.get("capital_score"), 0.5) * 0.20
            + val_score * 0.15
            + max(1 - risk, 0) * 0.15
        )
        if final >= 0.62 and risk < 0.8:
            trade_filter = "allow_long"
            multiplier = min(final, 0.8)
        elif final >= 0.45:
            trade_filter = "watch_only"
            multiplier = min(final * 0.5, 0.35)
        elif final >= 0.35:
            trade_filter = "reduce_only"
            multiplier = 0.0
        else:
            trade_filter = "block_long"
            multiplier = 0.0

        ranked.append(clean_value({
            **item,
            "name": snapshot.get("name") or item.get("name", ""),
            "market_cap": snapshot.get("total_market_val"),
            "turnover": snapshot.get("turnover"),
            "liquidity_score": liquidity,
            "valuation_score": val_score,
            "technical": tech,
            "capital": capital,
            "final_score": final,
            "confidence": min(0.85, 0.45 + 0.02 * min(len(history), 20)),
            "trade_filter": trade_filter,
            "position_multiplier": multiplier,
            "strategy_hint": "market_watchlist_cta" if trade_filter == "allow_long" else "watch_only",
        }))
    ranked.sort(key=lambda row: (row["trade_filter"] == "allow_long", row["final_score"], row["confidence"]), reverse=True)
    return ranked


def agent_refine(config_path: str, market: str, rows: list[dict[str, Any]], enable_web_search: bool) -> dict[str, Any] | None:
    config = load_config(config_path or None, PROJECT_ROOT)
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
你是 MarketWatchlistAgent。请基于输入候选的Futu行情、技术、资金和估值数据，为独立市场观察池排序。
只输出 JSON。不得编造事实，不得下单。trade_filter 只能为 allow_long/watch_only/reduce_only/block_long。
重点考虑：趋势强度、资金承接、流动性、估值质量、波动风险和市场主题。
""".strip()
    user_prompt = json.dumps({"market": market, "candidates": rows}, ensure_ascii=False)
    try:
        return client.complete_json(system_prompt, user_prompt)
    except LlmClientError:
        return None


def main() -> None:
    args = build_parser().parse_args()
    market, candidates = load_universe(Path(args.universe), args.market)
    ctx = OpenQuoteContext(args.host, args.port)
    try:
        ranked = local_rank(ctx, candidates, args.days)
    finally:
        ctx.close()

    selected = ranked[: args.top_n]
    agent_result = agent_refine(args.config, market, selected, args.enable_web_search) if args.enable_agent else None
    result = {
        "agent": "MarketWatchlistAgent",
        "market": market,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "universe": str(Path(args.universe)),
        "selected_targets": selected,
        "agent_result": agent_result,
        "data_quality": {
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "agent_used": bool(agent_result),
        },
    }
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT.joinpath(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(clean_value(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"市场观察池已输出: {output}")
    for item in selected:
        print(f"{item['vt_symbol']} filter={item['trade_filter']} score={item['final_score']:.2f} trend={item['technical']['trend_score']:.2f} risk={item['technical']['risk_score']:.2f}")


if __name__ == "__main__":
    main()
