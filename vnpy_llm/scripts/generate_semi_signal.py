from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy_llm.config import load_config
from vnpy_llm.llm_client import OpenAICompatibleClient
from vnpy_llm.news_feed import load_news_files
from vnpy_llm.rag_store import LocalRagStore
from vnpy_llm.report import write_signal_report
from vnpy_llm.risk import RiskPolicy, make_risk_decision
from vnpy_llm.scoring import score_evidence
from vnpy_llm.signal_store import SignalStore
from vnpy_llm.base import parse_datetime, utc_now


DEFAULT_TERMS = [
    "SOXL", "SOXX", "SMH", "NVDA", "AMD", "AVGO", "TSM", "ASML", "AMAT", "LRCX", "MU", "INTC",
    "Fed", "FOMC", "Powell", "rate cut", "inflation", "payroll", "CPI", "PCE", "PMI", "USD/JPY", "yen", "carry trade",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 SOXL 半导体与宏观 LLM 风控信号")
    parser.add_argument("--config", default="", help="配置文件路径，默认读取 .vntrader/llm_trading_setting.json")
    parser.add_argument("--news", action="append", default=[], help="新闻 JSON/JSONL/CSV/文本路径，可重复传入")
    parser.add_argument("--symbol", default="", help="目标标的，默认读取配置")
    parser.add_argument("--decision-time", default="", help="决策时间 ISO 格式，默认当前 UTC 时间")
    parser.add_argument("--max-items", type=int, default=30)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config or None, PROJECT_ROOT)
    symbol = (args.symbol or config.symbol).upper()
    decision_time = parse_datetime(args.decision_time) if args.decision_time else utc_now()

    news_paths = [Path(path) for path in args.news] or config.news_paths
    items = load_news_files(news_paths)
    store = LocalRagStore(items)
    evidence = store.search(DEFAULT_TERMS + [symbol], decision_time, max_items=args.max_items)

    client = OpenAICompatibleClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
        api_type=config.llm_api_type,
        api_user=config.api_user,
        enable_web_search=config.enable_web_search,
        temperature=config.temperature,
        conversation_id=config.conversation_id,
    )
    signal = score_evidence(symbol, evidence, client)
    signal_path = SignalStore(config.signal_dir).write(signal)

    policy = RiskPolicy(
        min_confidence=config.min_confidence,
        stale_after_hours=config.stale_after_hours,
        high_risk_threshold=config.high_risk_threshold,
        block_risk_threshold=config.block_risk_threshold,
        default_position_multiplier=config.default_position_multiplier,
    )
    decision = make_risk_decision(signal, policy, signal.scored_at)
    report_path = write_signal_report(config.report_dir, signal, decision)

    print(f"LLM信号已生成: {signal_path}")
    print(f"交易前摘要已生成: {report_path}")
    print(f"风控审批: allow_new_long={decision.allow_new_long}, multiplier={decision.position_multiplier:.2f}, reason={decision.reason}")


if __name__ == "__main__":
    main()
