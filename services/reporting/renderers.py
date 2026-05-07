from __future__ import annotations

from services.reporting.schemas import MiddayReport, PremarketReport


class TextReportRenderer:
    def render_premarket(self, report: PremarketReport) -> str:
        lines = []
        lines.append("【盘前环境】")
        for bullet in report.environment.bullets:
            lines.append(f"- {bullet}")

        lines.append("")
        lines.append("【观察池动态调整】")
        lines.append(f"- 新增：{', '.join(f'{i.symbol}({i.status_label or '-'})' for i in report.watchlist_diff.added) or '无'}")
        lines.append(f"- 继续保留：{', '.join(f'{i.symbol}({i.status_label or '-'})' for i in report.watchlist_diff.retained) or '无'}")
        lines.append(f"- 升级重点：{', '.join(f'{i.symbol}({i.status_label or '-'})' for i in report.watchlist_diff.promoted) or '无'}")
        lines.append(f"- 降级观察：{', '.join(f'{i.symbol}({i.status_label or '-'})' for i in report.watchlist_diff.weakened) or '无'}")
        lines.append(f"- 暂时移出：{', '.join(f'{i.symbol}({i.status_label or '-'})' for i in report.watchlist_diff.pending_removal) or '无'}")

        lines.append("")
        lines.append(f"【{report.market} Top候选】")
        for idx, candidate in enumerate(report.top_candidates, start=1):
            lines.append(f"{idx}. {candidate.symbol}/{candidate.name}")
            lines.append(f"   - 综合理由：{candidate.rationale}")
            lines.append(f"   - 主要风险：{candidate.risk}")
            lines.append(f"   - 综合置信度：{candidate.confidence or 0}/100")
            lines.append(f"   - 建议动作：{candidate.action}")

        lines.append("")
        lines.append("【观察池操作建议】")
        if report.actions:
            for item in report.actions:
                lines.append(f"- {item.symbol}/{item.name}：{item.action} —— {item.reason}")
        else:
            lines.append("- 其余以观察为主")

        lines.append("")
        lines.append("【今日结论】")
        for item in report.conclusion:
            lines.append(f"- {item}")

        return "\n".join(lines)

    def render_midday(self, report: MiddayReport) -> str:
        lines = []
        lines.append("【A股观察池中午表现】")
        lines.append(f"- 最强：{report.a_share.strongest}")
        lines.append(f"- 最弱：{report.a_share.weakest}")
        lines.append(f"- 关键变化：{report.a_share.key_change}")
        lines.append("")
        lines.append("【港股观察池中午表现】")
        lines.append(f"- 最强：{report.hong_kong.strongest}")
        lines.append(f"- 最弱：{report.hong_kong.weakest}")
        lines.append(f"- 关键变化：{report.hong_kong.key_change}")
        lines.append("")
        lines.append("【中午重点处理】")
        if report.actions:
            for item in report.actions:
                lines.append(f"- {item.symbol}/{item.name}：{item.action} —— {item.reason}")
        else:
            lines.append("- 中午暂无明显结构变化")
        lines.append("")
        lines.append("【一句话结论】")
        for item in report.conclusion:
            lines.append(f"- {item}")
        return "\n".join(lines)
