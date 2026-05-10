from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from services.evaluation_hub.adapters.knot_agent import KnotAgentEvaluationAdapter
from vnpy_llm.base import beijing_now_isoformat
from services.evaluation_hub.adapters.knot_agent_schema import local_strategy_fallback, validate_json_only_response
from vnpy_llm.llm_client import LlmClientError, OpenAICompatibleClient


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
        prompt = self.build_prompt(symbol=symbol, market=market, payload=payload, task_type=task_type)
        remote_error = ""
        remote = self._call_remote(prompt)
        if remote is None:
            fallback = local_strategy_fallback(payload)
            raw_response = json.dumps(fallback, ensure_ascii=False)
            runtime = 'remote-knot-fallback'
        else:
            raw_response = json.dumps(remote, ensure_ascii=False)
            runtime = 'knot-agui-remote'
        validated = validate_json_only_response(raw_response)
        parsed = validated.data if validated.ok else local_strategy_fallback(payload)
        return {
            'symbol': symbol,
            'market': market,
            'task_type': task_type,
            'decision_time': beijing_now_isoformat(),
            'payload': payload,
            'prompt': prompt,
            'raw_response': raw_response,
            'parsed': parsed,
            'runtime': runtime if validated.ok else 'remote-knot-fallback',
            'schema_validated': validated.ok,
            'fallback_used': remote is None or not validated.ok,
            'remote_error': remote_error,
        }

    def _call_remote(self, prompt: str) -> dict[str, Any] | None:
        base_url = os.environ.get('KNOT_AGUI_URL') or os.environ.get('KNOT_BASE_URL') or ''
        api_key = os.environ.get('KNOT_API_TOKEN') or ''
        if not base_url or not api_key:
            return None
        client = OpenAICompatibleClient(
            base_url=base_url,
            api_key=api_key,
            model=os.environ.get('KNOT_MODEL', ''),
            api_type='knot_agui',
            api_user=os.environ.get('KNOT_API_USER', ''),
            enable_web_search=os.environ.get('KNOT_ENABLE_WEB_SEARCH') == 'YES',
            timeout_seconds=int(os.environ.get('KNOT_TIMEOUT_SECONDS', '60')),
            conversation_id=os.environ.get('KNOT_CONVERSATION_ID', ''),
        )
        try:
            return client.complete_json('', prompt)
        except (LlmClientError, OSError, ValueError):
            return None

    def save_raw_output(self, name: str, data: dict[str, Any]) -> Path:
        path = self.out_dir / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return path
