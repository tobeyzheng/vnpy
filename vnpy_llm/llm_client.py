from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable


class LlmClientError(RuntimeError):
    pass


def _parse_json_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return json.loads(fenced.group(1))

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stripped[start : end + 1])
    raise LlmClientError("LLM response does not contain a JSON object")


def _extract_agui_text(lines: Iterable[bytes | str]) -> str:
    parts: list[str] = []
    errors: list[str] = []
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
        line = line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = str(event.get("type") or event.get("event") or "")
        raw_event = event.get("rawEvent") or {}
        if event_type in {"TEXT_MESSAGE_CONTENT", "TextMessageContent"}:
            parts.append(str(raw_event.get("content", event.get("delta", ""))))
        elif event_type == "agent_message":
            parts.append(str(event.get("data", "")))
        elif event_type in {"RUN_ERROR", "RunError", "error"}:
            tip = raw_event.get("tip_option", {}) if isinstance(raw_event, dict) else {}
            errors.append(str(tip.get("content") or event.get("data") or event))

    if errors:
        raise LlmClientError("; ".join(errors))
    return "".join(parts).strip()


@dataclass(frozen=True)
class OpenAICompatibleClient:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 30
    max_retries: int = 2
    api_type: str = "openai"
    api_user: str = ""
    enable_web_search: bool = False
    temperature: float = 0.0
    conversation_id: str = ""

    def is_knot_agui(self) -> bool:
        return self.api_type == "knot_agui" or "/agents/agui/" in self.base_url

    def is_configured(self) -> bool:
        if self.is_knot_agui():
            return bool(self.base_url and self.api_key)
        return bool(self.base_url and self.api_key and self.model)

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not self.is_configured():
            raise LlmClientError("LLM client is not configured")
        if self.is_knot_agui():
            return self._complete_json_knot_agui(system_prompt, user_prompt)
        return self._complete_json_openai(system_prompt, user_prompt)

    def _complete_json_openai(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        endpoint = self.base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint = f"{endpoint}/chat/completions"

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        raw = self._post_json(endpoint, payload, headers, stream=False)
        data = json.loads(raw)
        return _parse_json_text(data["choices"][0]["message"]["content"])

    def _complete_json_knot_agui(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        message = "\n\n".join(
            [
                system_prompt,
                "请严格只输出一个 JSON 对象，不要输出 Markdown 代码块或额外解释。",
                user_prompt,
            ]
        )
        payload_input: dict[str, Any] = {
            "message": message,
            "conversation_id": self.conversation_id,
            "stream": True,
            "enable_web_search": self.enable_web_search,
            "temperature": self.temperature,
        }
        if self.model:
            payload_input["model"] = self.model
        payload = {"input": payload_input}
        headers = {
            "x-knot-api-token": self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.api_user:
            headers["x-knot-api-user"] = self.api_user
            headers["X-Username"] = self.api_user

        raw_text = self._post_json(self.base_url.rstrip("/"), payload, headers, stream=True)
        return _parse_json_text(raw_text)

    def _post_json(self, endpoint: str, payload: dict[str, Any], headers: dict[str, str], stream: bool) -> str:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    if stream:
                        return _extract_agui_text(response)
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                last_error = LlmClientError(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}")
            except (LlmClientError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(min(2 ** attempt, 5))

        raise LlmClientError(f"LLM request failed: {last_error}")

