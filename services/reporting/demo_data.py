from __future__ import annotations

from services.candidate_engine import Candidate, SignalEvidence


def build_demo_candidates(market: str) -> list[Candidate]:
    if market == "us":
        return [
            Candidate("NVDA.US", "NVIDIA", "us", "AI/Chip", "趋势、事件催化与龙头属性共振", "高位波动放大", None, "回调再买", [SignalEvidence("trend", "technical", "uptrend intact", 0.88), SignalEvidence("earnings", "event", "AI demand narrative", 0.84)]),
            Candidate("AVGO.US", "Broadcom", "us", "Semiconductor", "AI链景气度延续", "高位滞涨后或先消化", None, "可小仓试错", [SignalEvidence("trend", "technical", "relative strength", 0.82), SignalEvidence("fundamental", "quality", "profit quality", 0.79)]),
            Candidate("MSFT.US", "Microsoft", "us", "Software/AI", "软件与AI应用双重属性", "防守风格下弹性或偏弱", None, "继续观察", [SignalEvidence("fundamental", "quality", "quality leader", 0.78), SignalEvidence("macro", "style", "safer AI exposure", 0.73)]),
        ]
    if market == "hong_kong":
        return [
            Candidate("00700.HK", "腾讯控股", "hong_kong", "Internet", "平台龙头与回购支撑增强", "若南下转弱弹性会下降", None, "继续持有", [SignalEvidence("fundamental", "quality", "cash flow strength", 0.81), SignalEvidence("trend", "technical", "relative stability", 0.74)]),
            Candidate("00981.HK", "中芯国际", "hong_kong", "Semiconductor", "半导体主线共振增强", "波动较大且受政策预期影响", None, "回调再买", [SignalEvidence("trend", "technical", "trend improving", 0.8), SignalEvidence("event", "industry", "chip narrative", 0.77)]),
            Candidate("01810.HK", "小米集团-W", "hong_kong", "Consumer Tech", "产品催化与风险偏好改善", "涨速过快时容易震荡", None, "可小幅试加", [SignalEvidence("trend", "technical", "price strength", 0.79), SignalEvidence("event", "product", "new cycle", 0.72)]),
        ]
    return [
        Candidate("300308.SZ", "中际旭创", "a_share", "Optical/AI", "AI算力链辨识度高", "高位分歧时回撤会更快", None, "回调再买", [SignalEvidence("trend", "technical", "leader strength", 0.84), SignalEvidence("sector", "theme", "AI infra", 0.8)]),
        Candidate("688041.SH", "海光信息", "a_share", "AI/Chip", "国产算力主线清晰", "高估值下对情绪敏感", None, "可小仓试错", [SignalEvidence("fundamental", "quality", "domestic chip logic", 0.78), SignalEvidence("trend", "technical", "breakout attempt", 0.76)]),
        Candidate("002371.SZ", "北方华创", "a_share", "Semiconductor Equipment", "设备链景气和国产替代逻辑仍在", "若主线切换会阶段承压", None, "继续观察", [SignalEvidence("fundamental", "quality", "equipment moat", 0.8), SignalEvidence("sector", "theme", "chip capex", 0.74)]),
    ]
