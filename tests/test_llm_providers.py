"""The provider adapters translate the same canonical conversation both ways.

Nothing above desk/llm.py knows which provider is active.
"""
from __future__ import annotations

import json

import pytest

from desk.llm import (AnthropicClient, LLMResponse, OpenAIClient, ToolCall, request_hash)

SYSTEM = "You are Seat 1."
TOOLS = [{"name": "match_deposit", "description": "match a deposit",
          "parameters": {"type": "object", "properties": {"bank_txn_id": {"type": "string"}},
                         "required": ["bank_txn_id"]}}]
MESSAGES = [
    {"role": "user", "content": "Record BNK-0026."},
    {"role": "assistant", "content": "Matching.",
     "tool_calls": [{"id": "c1", "name": "match_deposit",
                     "arguments": {"bank_txn_id": "BNK-0026"}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "match_deposit",
     "content": '{"principal": "13900.00"}'},
    {"role": "tool", "tool_call_id": "c2", "name": "match_deposit",
     "content": '{"interest": "417.00"}'},
]


def test_openai_message_translation():
    out = OpenAIClient.to_openai_messages(SYSTEM, MESSAGES)
    assert out[0] == {"role": "system", "content": SYSTEM}
    assert out[1] == {"role": "user", "content": "Record BNK-0026."}
    assert out[2]["tool_calls"][0]["function"]["name"] == "match_deposit"
    assert json.loads(out[2]["tool_calls"][0]["function"]["arguments"]) == {"bank_txn_id": "BNK-0026"}
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"principal": "13900.00"}'}
    assert out[4]["role"] == "tool"


def test_openai_tool_translation():
    tools = OpenAIClient.to_openai_tools(TOOLS)
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "match_deposit"
    assert tools[0]["function"]["parameters"] == TOOLS[0]["parameters"]


def test_anthropic_message_translation_merges_tool_results():
    out = AnthropicClient.to_anthropic_messages(MESSAGES)
    assert out[0] == {"role": "user", "content": "Record BNK-0026."}
    assert out[1]["role"] == "assistant"
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use"]
    assert out[1]["content"][1]["input"] == {"bank_txn_id": "BNK-0026"}
    # Two consecutive tool results become one user turn with two blocks.
    assert len(out) == 3
    assert out[2]["role"] == "user"
    assert [b["type"] for b in out[2]["content"]] == ["tool_result", "tool_result"]


def test_anthropic_tool_translation():
    tools = AnthropicClient.to_anthropic_tools(TOOLS)
    assert tools[0]["name"] == "match_deposit"
    assert tools[0]["input_schema"] == TOOLS[0]["parameters"]
    assert "parameters" not in tools[0]


def test_the_cache_key_separates_providers_and_models():
    base = ("openai", "model-a", SYSTEM, MESSAGES, TOOLS)
    assert request_hash(*base) == request_hash(*base)
    assert request_hash("anthropic", *base[1:]) != request_hash(*base)
    assert request_hash("openai", "model-b", *base[2:]) != request_hash(*base)
    assert request_hash("openai", "model-a", "other", *base[3:]) != request_hash(*base)
    assert request_hash("openai", "model-a", SYSTEM, MESSAGES, []) != request_hash(*base)


def test_canonical_response_round_trips():
    response = LLMResponse(text="hi", tool_calls=(ToolCall("c1", "propose", {"a": 1}),))
    assert LLMResponse.from_dict(response.to_dict()) == response
    assert response.as_assistant_message() == {
        "role": "assistant", "content": "hi",
        "tool_calls": [{"id": "c1", "name": "propose", "arguments": {"a": 1}}],
    }


def test_model_ids_are_never_hardcoded():
    """No model id literal anywhere in desk/."""
    import re
    from desk.config import CFG as config

    pattern = re.compile(r"(gpt-[0-9]|claude-[a-z0-9]|o[134]-(mini|preview))", re.I)
    offenders = []
    for path in (config.root / "desk").rglob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "model ids must come from .env only:\n" + "\n".join(offenders)


def test_an_empty_model_is_a_clear_error():
    from desk.llm import LLMError

    with pytest.raises(LLMError, match="LLM_MODEL is empty"):
        OpenAIClient("", "sk-test")
    with pytest.raises(LLMError, match="LLM_MODEL is empty"):
        AnthropicClient("", "sk-ant-test")


def test_build_client_rejects_an_unknown_provider(conn, cfg):
    import dataclasses

    from desk.llm import LLMError, build_client

    with pytest.raises(LLMError, match="not supported"):
        build_client(conn, dataclasses.replace(cfg, provider="gemini"))
