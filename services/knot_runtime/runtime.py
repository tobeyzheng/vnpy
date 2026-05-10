from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.evaluation_hub.adapters import local_strategy_fallback
from vnpy_llm.base import beijing_now_isoformat


class KnotAgentRuntime:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.out_dir = repo_root / 'state' / 'runs'

    def evaluate_symbol(self, *, symbol: str, market: str, payload: dict[str, Any], task_type: str = 'evaluation') -> dict[str, Any]:
        # 当前阶段：先把“实时调用”链路和原始落盘机制接好。
        # 真正远程 knot agent 调用后续替换这里；现在先保留 raw + parsed + fallback 结构。
        result = local_strategy_fallback(payload)
        raw = {
            'symbol': symbol,
            'market': market,
            'task_type': task_type,
            'decision_time': beijing_now_isoformat(),
            'payload': payload,
            'raw_response': json.dumps(result, ensure_ascii=False),
            'parsed': result,
            'runtime': 'local-fallback-until-remote-hooked',
        }
        return raw

    def save_raw_output(self, name: str, data: dict[str, Any]) -> Path:
        path = self.out_dir / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return path
