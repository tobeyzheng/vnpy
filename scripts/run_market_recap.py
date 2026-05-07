from __future__ import annotations

from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    content = """【昨日A股】
- 最强方向：AI算力链与高辨识度龙头延续强势
- 最弱方向：弱题材与高位跟风分化明显
- 延续跟踪：中际旭创、海光信息

【昨日港股】
- 最强方向：恒科与半导体链更强
- 最弱方向：地产与低弹性方向偏弱
- 延续跟踪：腾讯控股、中芯国际

【昨日美股】
- 最强方向：AI主线与半导体龙头
- 最弱方向：非主线成长股表现分化
- 延续跟踪：NVDA、AVGO、MSFT

【三市强弱排序】
- 1) 美股
- 2) A股
- 3) 港股

【今天优先看什么】
- 优先看AI/半导体主线是否继续获得资金确认。
- 如果龙头承接转弱，今天就更偏均衡而非进攻。
"""
    out_path = repo_root / "output_market_recap.txt"
    out_path.write_text(content, encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
