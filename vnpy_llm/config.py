from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _get_env(name: str) -> str:
    value = os.environ.get(name, "")
    if value or os.name != "nt":
        return value
    try:
        import winreg

        for root in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
            with winreg.OpenKey(root, "Environment") as key:
                try:
                    registry_value, _ = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if registry_value:
                    return str(registry_value)
    except OSError:
        return ""
    return ""



@dataclass(frozen=True)
class LlmTradingConfig:
    symbol: str = "SOXL.SMART"
    model: str = ""
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    api_user_env: str = ""
    llm_api_type: str = "openai"
    enable_web_search: bool = False
    temperature: float = 0.0
    conversation_id: str = ""
    signal_dir: Path = Path("examples/futu_trader/output/llm_signals")
    report_dir: Path = Path("examples/futu_trader/output/llm_reports")
    news_paths: list[Path] = field(default_factory=list)
    timeout_seconds: int = 30
    max_retries: int = 2
    min_confidence: float = 0.55
    stale_after_hours: int = 30
    high_risk_threshold: float = 0.75
    block_risk_threshold: float = 0.85
    default_position_multiplier: float = 0.0
    real_trading_allowed: bool = False

    @property
    def api_key(self) -> str:
        return _get_env(self.api_key_env)

    @property
    def api_user(self) -> str:
        return _get_env(self.api_user_env) if self.api_user_env else ""




def _as_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir.joinpath(path)


def load_config(path: str | Path | None = None, base_dir: str | Path | None = None) -> LlmTradingConfig:
    root = Path(base_dir or Path.cwd()).resolve()
    if path is None:
        default_path = root.joinpath(".vntrader", "llm_trading_setting.json")
        data: dict[str, Any] = {}
        if default_path.exists():
            data = json.loads(default_path.read_text(encoding="utf-8"))
    else:
        config_path = _as_path(path, root)
        data = json.loads(config_path.read_text(encoding="utf-8"))

    news_paths = [_as_path(item, root) for item in data.get("news_paths", [])]
    return LlmTradingConfig(
        symbol=str(data.get("symbol", "SOXL.SMART")),
        model=str(data.get("model", os.environ.get("OPENAI_MODEL", ""))),
        base_url=str(data.get("base_url", os.environ.get("OPENAI_BASE_URL", ""))),
        api_key_env=str(data.get("api_key_env", "OPENAI_API_KEY")),
        api_user_env=str(data.get("api_user_env", "")),
        llm_api_type=str(data.get("llm_api_type", data.get("api_type", "openai"))),
        enable_web_search=bool(data.get("enable_web_search", False)),
        temperature=float(data.get("temperature", 0.0)),
        conversation_id=str(data.get("conversation_id", "")),
        signal_dir=_as_path(data.get("signal_dir", "examples/futu_trader/output/llm_signals"), root),
        report_dir=_as_path(data.get("report_dir", "examples/futu_trader/output/llm_reports"), root),
        news_paths=news_paths,
        timeout_seconds=int(data.get("timeout_seconds", 30)),
        max_retries=int(data.get("max_retries", 2)),
        min_confidence=float(data.get("min_confidence", 0.55)),
        stale_after_hours=int(data.get("stale_after_hours", 30)),
        high_risk_threshold=float(data.get("high_risk_threshold", 0.75)),
        block_risk_threshold=float(data.get("block_risk_threshold", 0.85)),
        default_position_multiplier=float(data.get("default_position_multiplier", 0.0)),
        real_trading_allowed=bool(data.get("real_trading_allowed", False)),
    )
