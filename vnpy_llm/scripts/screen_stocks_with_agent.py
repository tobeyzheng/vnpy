from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy_llm.base import EvidenceItem, format_datetime, parse_datetime, utc_now
from vnpy_llm.config import load_config
from vnpy_llm.llm_client import OpenAICompatibleClient
from vnpy_llm.news_feed import load_news_files
from vnpy_llm.rag_store import LocalRagStore
from vnpy_llm.signal_store import SignalStore

DEFAULT_US_UNIVERSE = [
    {"symbol": "SPY", "vt_symbol": "SPY.SMART", "name": "SPDR S&P 500 ETF", "asset_type": "ETF", "sector": "Broad Market"},
    {"symbol": "QQQ", "vt_symbol": "QQQ.SMART", "name": "Invesco QQQ Trust", "asset_type": "ETF", "sector": "Nasdaq 100"},
    {"symbol": "SOXL", "vt_symbol": "SOXL.SMART", "name": "Direxion Daily Semiconductor Bull 3X", "asset_type": "ETF", "sector": "Semiconductor"},
    {"symbol": "SOXX", "vt_symbol": "SOXX.SMART", "name": "iShares Semiconductor ETF", "asset_type": "ETF", "sector": "Semiconductor"},
    {"symbol": "SMH", "vt_symbol": "SMH.SMART", "name": "VanEck Semiconductor ETF", "asset_type": "ETF", "sector": "Semiconductor"},
    {"symbol": "TQQQ", "vt_symbol": "TQQQ.SMART", "name": "ProShares UltraPro QQQ", "asset_type": "ETF", "sector": "Leveraged Tech"},
    {"symbol": "NVDA", "vt_symbol": "NVDA.SMART", "name": "NVIDIA", "asset_type": "Stock", "sector": "Semiconductor"},
    {"symbol": "AMD", "vt_symbol": "AMD.SMART", "name": "Advanced Micro Devices", "asset_type": "Stock", "sector": "Semiconductor"},
    {"symbol": "AVGO", "vt_symbol": "AVGO.SMART", "name": "Broadcom", "asset_type": "Stock", "sector": "Semiconductor"},
    {"symbol": "TSM", "vt_symbol": "TSM.SMART", "name": "Taiwan Semiconductor ADR", "asset_type": "Stock", "sector": "Semiconductor"},
    {"symbol": "ASML", "vt_symbol": "ASML.SMART", "name": "ASML Holding ADR", "asset_type": "Stock", "sector": "Semiconductor"},
    {"symbol": "AMAT", "vt_symbol": "AMAT.SMART", "name": "Applied Materials", "asset_type": "Stock", "sector": "Semiconductor Equipment"},
    {"symbol": "LRCX", "vt_symbol": "LRCX.SMART", "name": "Lam Research", "asset_type": "Stock", "sector": "Semiconductor Equipment"},
    {"symbol": "MU", "vt_symbol": "MU.SMART", "name": "Micron Technology", "asset_type": "Stock", "sector": "Memory"},
    {"symbol": "INTC", "vt_symbol": "INTC.SMART", "name": "Intel", "asset_type": "Stock", "sector": "Semiconductor"},
]

SCREENING_SYSTEM_PROMPT = """
你是 MarketResearchScreenerAgent，服务于量化交易系统，负责新闻搜集、宏观风险识别、股票/ETF 候选标的筛选和结构化信号生成。

你必须遵守：
1. 只基于输入证据和候选池判断，不得编造事实、价格、新闻或来源。
2. 当前持仓为 0，目标是筛选后续可进入量化回测/模拟交易观察池的标的，不是直接下单。
3. 只能输出严格 JSON 对象，不要输出 Markdown 或解释性前后缀。
4. 重点关注美联储态度、美国经济趋势、美债收益率、日元汇率/套息交易风险、VIX、行业事件、个股新闻、流动性、趋势和事件驱动。
5. 评分字段必须是 0 到 1 的数字；news_sentiment 可为 -1 到 1。
6. trade_filter 只能是 allow_long、watch_only、reduce_only、block_long。
7. position_multiplier 必须在 0 到 1 之间。
8. 如果证据不足，降低 confidence，并倾向 watch_only。
9. 不允许直接下单，不允许绕过风控。
""".strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调用 Knot Agent 进行量化候选标的筛选")
    parser.add_argument("--config", default="", help="配置文件路径，默认读取 .vntrader/llm_trading_setting.json")
    parser.add_argument("--market", default="US")
    parser.add_argument("--symbols", default="", help="逗号分隔候选代码，如 SOXL,NVDA,QQQ；为空则使用默认美股候选池")
    parser.add_argument("--universe", default="", help="候选池文件，支持 JSON/JSONL/CSV/TXT")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--max-evidence", type=int, default=40)
    parser.add_argument("--decision-time", default="", help="决策时间 ISO 格式，默认当前 UTC 时间")
    parser.add_argument("--current-cash", type=float, default=10_000)
    parser.add_argument("--current-positions", default="{}", help="当前持仓 JSON，默认空仓 {}")
    parser.add_argument("--risk-profile", default="moderate", choices=["conservative", "moderate", "aggressive"])
    parser.add_argument("--enable-web-search", action="store_true", help="覆盖配置，允许 Knot Agent 联网搜索")
    parser.add_argument("--output", default="", help="输出 JSON 路径，默认写入 output/agent_screening")
    return parser


def symbol_to_candidate(symbol: str, market: str) -> dict[str, Any]:
    text = symbol.strip().upper()
    if not text:
        return {}
    vt_symbol = text if "." in text else f"{text}.SMART" if market.upper() == "US" else text
    return {
        "symbol": text.split(".")[0],
        "vt_symbol": vt_symbol,
        "name": "",
        "asset_type": "Stock",
        "sector": "",
    }


def load_universe(path: str | Path, market: str) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"universe file not found: {file_path}")
    suffix = file_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        rows = data.get("candidates", data.get("items", data)) if isinstance(data, dict) else data
    elif suffix == ".jsonl":
        rows = [json.loads(line) for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    elif suffix == ".csv":
        with file_path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        rows = [symbol_to_candidate(line, market) for line in file_path.read_text(encoding="utf-8").splitlines()]

    candidates: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, str):
            candidate = symbol_to_candidate(row, market)
        else:
            symbol = str(row.get("symbol") or row.get("code") or row.get("vt_symbol") or "")
            candidate = {**symbol_to_candidate(symbol, market), **row}
            if not candidate.get("vt_symbol"):
                candidate["vt_symbol"] = symbol_to_candidate(symbol, market).get("vt_symbol", "")
        if candidate.get("symbol"):
            candidates.append(candidate)
    return candidates


def get_candidates(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.universe:
        return load_universe(args.universe, args.market)
    if args.symbols:
        return [candidate for candidate in (symbol_to_candidate(item, args.market) for item in args.symbols.split(",")) if candidate]
    return DEFAULT_US_UNIVERSE.copy() if args.market.upper() == "US" else []


def compact_evidence(evidence: list[EvidenceItem]) -> list[dict[str, Any]]:
    return [
        {
            "item_id": item.item_id,
            "title": item.title,
            "snippet": item.snippet[:500],
            "source": item.source,
            "source_time": format_datetime(item.source_time),
            "url": item.url,
            "score": item.score,
        }
        for item in evidence
    ]


def build_user_prompt(
    market: str,
    decision_time: datetime,
    candidates: list[dict[str, Any]],
    evidence: list[EvidenceItem],
    latest_llm_signal: dict[str, Any] | None,
    current_positions: dict[str, Any],
    current_cash: float,
    risk_profile: str,
    top_n: int,
) -> str:
    payload = {
        "task_objective": "当前仓位为0，从候选池中筛选适合作为后续量化回测/模拟交易观察标的的股票或ETF。",
        "decision_time": format_datetime(decision_time),
        "market": market.upper(),
        "current_cash": current_cash,
        "current_positions": current_positions,
        "risk_profile": risk_profile,
        "top_n": top_n,
        "candidate_universe": candidates,
        "macro_llm_signal": latest_llm_signal,
        "evidence": compact_evidence(evidence),
    }
    schema = {
        "agent": "MarketResearchScreenerAgent",
        "as_of": "ISO8601",
        "decision_time": "ISO8601",
        "market": market.upper(),
        "selected_targets": [
            {
                "rank": 1,
                "symbol": "SOXL",
                "vt_symbol": "SOXL.SMART",
                "name": "",
                "asset_type": "ETF/Stock",
                "sector": "",
                "liquidity_score": 0.0,
                "trend_score": 0.0,
                "volatility_score": 0.0,
                "news_sentiment": 0.0,
                "event_score": 0.0,
                "macro_sensitivity": 0.0,
                "risk_score": 0.0,
                "final_score": 0.0,
                "confidence": 0.0,
                "trade_filter": "watch_only",
                "position_multiplier": 0.0,
                "eligible_for_quant_trade": False,
                "strategy_hint": "cta_candidate/event_candidate/market_beta_candidate/watch_only",
                "reasons": [],
                "risks": [],
                "sources": [],
            }
        ],
        "rejected_candidates": [],
        "portfolio_guidance": {
            "allow_new_long_candidates": 0,
            "max_total_position_pct": 0.0,
            "notes": [],
        },
        "data_quality": {
            "evidence_count": len(evidence),
            "candidate_count": len(candidates),
            "confidence": 0.0,
            "warnings": [],
        },
    }
    return (
        "请根据输入 JSON 筛选候选标的，并严格按输出 schema 返回 JSON。\n"
        "输入 JSON：\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
        "输出 schema 示例：\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


def as_float(value: Any, default: float = 0.0, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        if isinstance(value, str) and value.endswith("%"):
            number = float(value[:-1]) / 100.0
        else:
            number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def normalize_result(payload: dict[str, Any], candidates: list[dict[str, Any]], top_n: int, decision_time: datetime, market: str) -> dict[str, Any]:
    by_symbol = {str(item.get("symbol", "")).upper(): item for item in candidates}
    allowed_filters = {"allow_long", "watch_only", "reduce_only", "block_long"}
    selected = []
    for row in payload.get("selected_targets", []):
        symbol = str(row.get("symbol", "")).upper()
        base = by_symbol.get(symbol, {})
        trade_filter = str(row.get("trade_filter", "watch_only"))
        if trade_filter not in allowed_filters:
            trade_filter = "watch_only"
        confidence = as_float(row.get("confidence"), 0.0)
        final_score = as_float(row.get("final_score"), 0.0)
        position_multiplier = as_float(row.get("position_multiplier"), 0.0)
        eligible = bool(row.get("eligible_for_quant_trade", False)) and trade_filter == "allow_long" and confidence >= 0.55
        selected.append(
            {
                "rank": int(row.get("rank") or len(selected) + 1),
                "symbol": symbol,
                "vt_symbol": str(row.get("vt_symbol") or base.get("vt_symbol") or symbol_to_candidate(symbol, market).get("vt_symbol", "")),
                "name": str(row.get("name") or base.get("name") or ""),
                "asset_type": str(row.get("asset_type") or base.get("asset_type") or "Stock"),
                "sector": str(row.get("sector") or base.get("sector") or ""),
                "liquidity_score": as_float(row.get("liquidity_score"), 0.0),
                "trend_score": as_float(row.get("trend_score"), 0.0),
                "volatility_score": as_float(row.get("volatility_score"), 0.0),
                "news_sentiment": as_float(row.get("news_sentiment"), 0.0, -1.0, 1.0),
                "event_score": as_float(row.get("event_score"), 0.0),
                "macro_sensitivity": as_float(row.get("macro_sensitivity"), 0.0),
                "risk_score": as_float(row.get("risk_score"), 1.0),
                "final_score": final_score,
                "confidence": confidence,
                "trade_filter": trade_filter,
                "position_multiplier": position_multiplier,
                "eligible_for_quant_trade": eligible,
                "strategy_hint": str(row.get("strategy_hint") or "watch_only"),
                "reasons": [str(item) for item in row.get("reasons", [])],
                "risks": [str(item) for item in row.get("risks", [])],
                "sources": [str(item) for item in row.get("sources", [])],
            }
        )
    selected.sort(key=lambda item: (item["eligible_for_quant_trade"], item["final_score"], item["confidence"]), reverse=True)
    selected = selected[:top_n]
    for idx, row in enumerate(selected, start=1):
        row["rank"] = idx

    return {
        "agent": "MarketResearchScreenerAgent",
        "as_of": payload.get("as_of") or format_datetime(utc_now()),
        "decision_time": payload.get("decision_time") or format_datetime(decision_time),
        "market": payload.get("market") or market.upper(),
        "selected_targets": selected,
        "rejected_candidates": payload.get("rejected_candidates", []),
        "portfolio_guidance": payload.get("portfolio_guidance", {}),
        "data_quality": payload.get("data_quality", {}),
    }


def write_markdown_report(result: dict[str, Any], path: Path) -> Path:
    lines = [
        "# Agent 选股结果",
        "",
        f"- as_of：`{result.get('as_of', '')}`",
        f"- decision_time：`{result.get('decision_time', '')}`",
        f"- market：`{result.get('market', '')}`",
        "",
        "## 入选标的",
        "",
        "| rank | symbol | vt_symbol | filter | multiplier | score | confidence | reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in result.get("selected_targets", []):
        reason = "; ".join(item.get("reasons", [])[:2]).replace("|", "/")
        lines.append(
            "| {rank} | {symbol} | {vt_symbol} | {trade_filter} | {position_multiplier:.2f} | {final_score:.2f} | {confidence:.2f} | {reason} |".format(
                reason=reason,
                **item,
            )
        )
    lines.extend(["", "## 组合提示", "", json.dumps(result.get("portfolio_guidance", {}), ensure_ascii=False, indent=2)])
    report_path = path.with_suffix(".md")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config or None, PROJECT_ROOT)
    decision_time = parse_datetime(args.decision_time) if args.decision_time else utc_now()
    candidates = get_candidates(args)
    if not candidates:
        raise SystemExit("候选池为空，请通过 --symbols 或 --universe 指定候选标的")

    current_positions = json.loads(args.current_positions)
    news_items = load_news_files(config.news_paths)
    query_terms = [item.get("symbol", "") for item in candidates] + ["Fed", "FOMC", "CPI", "PCE", "PMI", "USD/JPY", "yen", "carry trade"]
    evidence = LocalRagStore(news_items).search(query_terms, decision_time, max_items=args.max_evidence)
    latest_signal = SignalStore(config.signal_dir).latest(config.symbol, decision_time)

    client = OpenAICompatibleClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        timeout_seconds=max(config.timeout_seconds, 60),
        max_retries=config.max_retries,
        api_type=config.llm_api_type,
        api_user=config.api_user,
        enable_web_search=args.enable_web_search or config.enable_web_search,
        temperature=config.temperature,
        conversation_id=config.conversation_id,
    )
    if not client.is_configured():
        raise SystemExit("LLM/Agent 未配置，请检查 KNOT_API_TOKEN、KNOT_API_USER 和 llm_trading_setting.json")

    user_prompt = build_user_prompt(
        args.market,
        decision_time,
        candidates,
        evidence,
        latest_signal.to_dict() if latest_signal else None,
        current_positions,
        args.current_cash,
        args.risk_profile,
        args.top_n,
    )
    raw = client.complete_json(SCREENING_SYSTEM_PROMPT, user_prompt)
    result = normalize_result(raw, candidates, args.top_n, decision_time, args.market)

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT.joinpath(output_path)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = PROJECT_ROOT.joinpath("examples", "futu_trader", "output", "agent_screening", f"stock_screening_{stamp}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = write_markdown_report(result, output_path)

    print(f"Agent选股结果已输出: {output_path}")
    print(f"Agent选股报告已输出: {report_path}")
    for item in result.get("selected_targets", []):
        print(
            f"#{item['rank']} {item['vt_symbol']} filter={item['trade_filter']} "
            f"multiplier={item['position_multiplier']:.2f} score={item['final_score']:.2f} confidence={item['confidence']:.2f}"
        )


if __name__ == "__main__":
    main()
