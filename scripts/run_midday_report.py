from __future__ import annotations

from pathlib import Path

from services.reporting import ActionLine, TextReportRenderer
from services.reporting.schemas import MiddayMarketSummary, MiddayReport


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    report = MiddayReport(
        a_share=MiddayMarketSummary(
            strongest="光模块/AI算力方向相对最强，中际旭创与新易盛辨识度更高",
            weakest="高位分歧股承压，追高资金明显谨慎",
            key_change="板块仍有共振，但午间更像强主线内部分化，不是全面扩散",
        ),
        hong_kong=MiddayMarketSummary(
            strongest="中芯国际与科技权重相对更强，恒科方向承接尚可",
            weakest="弱地产与低辨识度个股偏弱",
            key_change="港股更受恒科与南下资金方向影响，今天偏结构性机会",
        ),
        actions=[
            ActionLine(symbol="300308.SZ", name="中际旭创", action="继续持有", reason="主线辨识度仍在，但不宜午间追高加速段"),
            ActionLine(symbol="00981.HK", name="中芯国际", action="仅观察", reason="强但波动大，等更清晰回踩或放量确认"),
        ],
        conclusion=[
            "A/H 中午更偏均衡，不是全面进攻时点。",
            "下午重点盯主线龙头承接，而不是边缘题材补涨。",
        ],
    )
    out_path = repo_root / "output_midday_report.txt"
    out_path.write_text(TextReportRenderer().render_midday(report), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
