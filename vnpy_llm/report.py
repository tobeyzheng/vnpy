from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .base import LlmSignal, RiskDecision, format_datetime, utc_now


def render_signal_report(signal: LlmSignal, decision: RiskDecision) -> str:
    lines = [
        f"# LLM 交易前摘要 - {signal.symbol}",
        "",
        f"- 信号 ID：`{signal.signal_id}`",
        f"- as_of：`{format_datetime(signal.as_of)}`",
        f"- valid_until：`{format_datetime(signal.valid_until)}`",
        f"- trade_filter：`{signal.trade_filter}`",
        f"- position_multiplier：`{signal.position_multiplier:.2f}`",
        f"- sector_sentiment：`{signal.sector_sentiment:.2f}`",
        f"- sector_risk：`{signal.sector_risk:.2f}`",
        f"- macro_risk：`{signal.macro_risk:.2f}`",
        f"- fed_policy_bias：`{signal.fed_policy_bias}`",
        f"- jpy_fx_risk：`{signal.jpy_fx_risk:.2f}`",
        f"- us_economy_score：`{signal.us_economy_score:.2f}`",
        f"- confidence：`{signal.confidence:.2f}`",
        "",
        "## 风控审批",
        "",
        f"- allow_new_long：`{decision.allow_new_long}`",
        f"- reduce_only：`{decision.reduce_only}`",
        f"- approved_multiplier：`{decision.position_multiplier:.2f}`",
        f"- reason：`{decision.reason}`",
        "",
        "## 主要原因",
        "",
    ]
    lines.extend([f"- {reason}" for reason in signal.reasons] or ["- 无"])
    lines.extend(["", "## 来源", ""])
    lines.extend([f"- {source}" for source in signal.sources] or ["- 无"])
    return "\n".join(lines) + "\n"


def write_signal_report(report_dir: str | Path, signal: LlmSignal, decision: RiskDecision) -> Path:
    folder = Path(report_dir)
    folder.mkdir(parents=True, exist_ok=True)
    safe_symbol = signal.symbol.replace(".", "_").replace("/", "_")
    path = folder.joinpath(f"{safe_symbol}_{signal.as_of.date().isoformat()}_pretrade.md")
    path.write_text(render_signal_report(signal, decision), encoding="utf-8")
    return path


def write_json_report(path: str | Path, payload: dict) -> Path:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": format_datetime(utc_now()), **payload}
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def append_audit_line(path: str | Path, payload: dict) -> None:
    audit_path = Path(path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": format_datetime(datetime.now()), **payload}
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
