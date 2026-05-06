from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy_llm.trusted_sources import fetch_trusted_source, load_trusted_sources, write_news_jsonl

DEFAULT_SOURCES_PATH = PROJECT_ROOT.joinpath("examples/futu_trader/config/trusted_news_sources.json")
DEFAULT_OUTPUT_PATH = PROJECT_ROOT.joinpath("examples/futu_trader/output/trusted_news/trusted_news.jsonl")
DEFAULT_SETTING_PATH = PROJECT_ROOT.joinpath(".vntrader/llm_trading_setting.json")


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else PROJECT_ROOT.joinpath(value)


def _load_setting() -> dict[str, Any]:
    if DEFAULT_SETTING_PATH.exists():
        return json.loads(DEFAULT_SETTING_PATH.read_text(encoding="utf-8"))
    return {}


def build_parser() -> argparse.ArgumentParser:
    setting = _load_setting()
    parser = argparse.ArgumentParser(description="采集可信新闻源并输出 JSONL")
    parser.add_argument("--sources", default=setting.get("trusted_news_sources_path", str(DEFAULT_SOURCES_PATH)))
    parser.add_argument("--output", default=setting.get("trusted_news_output_path", str(DEFAULT_OUTPUT_PATH)))
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--source-id", action="append", default=[], help="只采集指定 source_id，可重复传入")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    sources_path = _resolve(args.sources)
    output_path = _resolve(args.output)
    wanted = set(args.source_id)

    sources = load_trusted_sources(sources_path)
    if wanted:
        sources = [source for source in sources if source.source_id in wanted]

    all_items = []
    failures = []
    for source in sources:
        try:
            items = fetch_trusted_source(source, timeout_seconds=args.timeout)
            all_items.extend(items)
            print(f"{source.source_id}: {len(items)} items")
        except Exception as exc:  # noqa: BLE001 - 单个源失败不应中断整批采集
            failures.append((source.source_id, str(exc)))
            print(f"{source.source_id}: failed: {exc}")

    write_news_jsonl(all_items, output_path)
    print(f"可信新闻已写入: {output_path}, items={len(all_items)}, failures={len(failures)}")
    if failures:
        print("失败源:")
        for source_id, reason in failures:
            print(f"- {source_id}: {reason}")


if __name__ == "__main__":
    main()
