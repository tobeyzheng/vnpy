from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy_llm.trusted_sources import write_default_sources

DEFAULT_SOURCES_PATH = Path("examples/futu_trader/config/trusted_news_sources.json")
DEFAULT_OUTPUT_PATH = Path("examples/futu_trader/output/trusted_news/trusted_news.jsonl")
DEFAULT_SETTING_PATH = Path(".vntrader/llm_trading_setting.json")


def _rel(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def setup_sources(sources_path: Path, output_path: Path, setting_path: Path, overwrite_sources: bool = False) -> None:
    sources_path = sources_path if sources_path.is_absolute() else PROJECT_ROOT.joinpath(sources_path)
    output_path = output_path if output_path.is_absolute() else PROJECT_ROOT.joinpath(output_path)
    setting_path = setting_path if setting_path.is_absolute() else PROJECT_ROOT.joinpath(setting_path)

    write_default_sources(sources_path, overwrite=overwrite_sources)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    setting_path.parent.mkdir(parents=True, exist_ok=True)

    setting = _load_json(setting_path)
    news_paths = [str(item) for item in setting.get("news_paths", [])]
    output_rel = _rel(output_path)
    if output_rel not in news_paths and output_path.as_posix() not in news_paths:
        news_paths.append(output_rel)

    setting.setdefault("symbol", "SOXL.SMART")
    setting.setdefault("model", "")
    setting.setdefault("base_url", "")
    setting.setdefault("api_key_env", "OPENAI_API_KEY")
    setting.setdefault("api_user_env", "")
    setting.setdefault("llm_api_type", "openai")
    setting.setdefault("enable_web_search", False)
    setting.setdefault("temperature", 0.0)
    setting.setdefault("conversation_id", "")
    setting.setdefault("signal_dir", "examples/futu_trader/output/llm_signals")
    setting.setdefault("report_dir", "examples/futu_trader/output/llm_reports")
    setting["news_paths"] = news_paths
    setting["trusted_news_sources_path"] = _rel(sources_path)
    setting["trusted_news_output_path"] = output_rel
    setting.setdefault("timeout_seconds", 30)
    setting.setdefault("max_retries", 2)
    setting.setdefault("min_confidence", 0.55)
    setting.setdefault("stale_after_hours", 30)
    setting.setdefault("high_risk_threshold", 0.75)
    setting.setdefault("block_risk_threshold", 0.85)
    setting.setdefault("default_position_multiplier", 0.0)
    setting.setdefault("real_trading_allowed", False)

    setting_path.write_text(json.dumps(setting, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"可信新闻源配置: {sources_path}")
    print(f"新闻输出路径: {output_path}")
    print(f"交易配置已更新: {setting_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="设置 LLM 交易系统可信新闻源")
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES_PATH), help="可信新闻源配置 JSON 路径")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="采集后的新闻 JSONL 输出路径")
    parser.add_argument("--setting", default=str(DEFAULT_SETTING_PATH), help="LLM 交易配置路径")
    parser.add_argument("--overwrite-sources", action="store_true", help="覆盖已有新闻源配置")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    setup_sources(Path(args.sources), Path(args.output), Path(args.setting), args.overwrite_sources)


if __name__ == "__main__":
    main()
