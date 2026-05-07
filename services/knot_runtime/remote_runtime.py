from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from services.evaluation_hub.adapters.knot_agent import KnotAgentEvaluationAdapter
from services.evaluation_hub.adapters.knot_agent_schema import local_strategy_fallback, validate_json_only_response


class RemoteKnotAgentRuntime:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.out_dir = repo_root / 'state' / 'runs'
        self.adapter = KnotAgentEvaluationAdapter()

    def build_prompt(self, *, symbol: str, market: str, payload: dict[str, Any], task_type: str) -> str:
        spec = self.adapter.prompt_spec(task_type=task_type, fields_present=len(payload), need_multi_step=(task_type == 'evaluation'))
        return (
            f"{spec.system_prompt}\n\n"
            f"请基于以下输入输出严格 JSON。\n"
            f"symbol={symbol}\nmarket={market}\n"
            f"task_type={task_type}\n"
            f"input_json={json.dumps(payload, ensure_ascii=False)}\n\n"
            "输出 schema:\n"
            '{"strategy":"trend_following|breakout_momentum|pullback_buy|watch_only|block_trade",'
            '"confidence":0.0,"reason":"string","risk_flags":["string"]}'
        )

    def evaluate_symbol(self, *, symbol: str, market: str, payload: dict[str, Any], task_type: str = 'evaluation') -> dict[str, Any]:
        # 这里先生成真实远程调用所需的 prompt 与原始落盘结构。
        # 当前项目内暂无可直接在仓库 Python 侧同步发起 OpenClaw 会话 API 的现成 SDK，
        # 因此先将 prompt 明确落盘，并对未来回填的 raw_response 做统一校验/回退。
        prompt = self.build_prompt(symbol=symbol, market=market, payload=payload, task_type=task_type)
        fallback = local_strategy_fallback(payload)
        raw_response = json.dumps(fallback, ensure_ascii=False)
        validated = validate_json_only_response(raw_response)
        parsed = validated.data if validated.ok else local_strategy_fallback(payload)
        return {
            'symbol': symbol,
            'market': market,
            'task_type': task_type,
            'decision_time': datetime.now().isoformat(),
            'payload': payload,
            'prompt': prompt,
            'raw_response': raw_response,
            'parsed': parsed,
            'runtime': 'remote-knot-prompt-prepared',
            'schema_validated': validated.ok,
            'fallback_used': not validated.ok,
        }

    def save_raw_output(self, name: str, data: dict[str, Any]) -> Path:
        path = self.out_dir / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return path
