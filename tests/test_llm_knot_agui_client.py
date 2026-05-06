from __future__ import annotations

from vnpy_llm.llm_client import _extract_agui_text, _parse_json_text


def test_extract_knot_agui_text_from_raw_event() -> None:
    lines = [
        'data: {"type":"TEXT_MESSAGE_START","rawEvent":{"conversation_id":"c1"}}',
        'data: {"type":"TEXT_MESSAGE_CONTENT","rawEvent":{"content":"{\\\"trade_filter\\\":"}}',
        'data: {"type":"TEXT_MESSAGE_CONTENT","rawEvent":{"content":"\\\"allow_long\\\"}"}}',
        'data: [DONE]',
    ]
    assert _extract_agui_text(lines) == '{"trade_filter":"allow_long"}'


def test_parse_json_text_from_markdown_fence() -> None:
    payload = _parse_json_text('```json\n{"confidence": 0.8}\n```')
    assert payload["confidence"] == 0.8
