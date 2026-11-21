"""The same Phase 1 flow runs through both provider adapters, no network.

Stub transports stand in for the OpenAI and Anthropic SDK clients and return
provider-shaped payloads. Everything above desk/llm.py is untouched: the agent,
the tools, the permissions and the audit log are the same in both runs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from desk import agent, gateway, llm, personas
from desk.__main__ import TASK
from desk.scripted_seat1 import script

from conftest import BANK_TXN

JE3 = {
    "Assets:Bank:Increase:Operating": Decimal("14317.00"),
    "Assets:Receivable:TariffRefund": Decimal("-13900.00"),
    "Income:Interest:TariffRefund": Decimal("-417.00"),
}


# --- stubs shaped like each SDK's response objects ------------------------- #

@dataclass
class _Fn:
    name: str
    arguments: str


@dataclass
class _OpenAICall:
    id: str
    function: _Fn
    type: str = "function"


@dataclass
class _OpenAIMessage:
    content: str | None
    tool_calls: list[_OpenAICall] | None


@dataclass
class _OpenAIChoice:
    message: _OpenAIMessage


@dataclass
class _OpenAIResponse:
    choices: list[_OpenAIChoice]


@dataclass
class _Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class _AnthropicResponse:
    content: list[_Block]


class _Transport:
    """Replays the scripted responses in the shape the SDK would return."""

    def __init__(self, provider: str):
        self.provider = provider
        self.script = script()
        self.requests: list[dict[str, Any]] = []

    def _next(self) -> llm.LLMResponse:
        assert self.script, "transport script exhausted"
        return self.script.pop(0)

    # OpenAI surface: client.chat.completions.create(...)
    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    # Anthropic surface: client.messages.create(...)
    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        response = self._next()
        if self.provider == "openai":
            calls = [
                _OpenAICall(c.id, _Fn(c.name, json.dumps(c.arguments)))
                for c in response.tool_calls
            ] or None
            return _OpenAIResponse([_OpenAIChoice(_OpenAIMessage(response.text, calls))])
        blocks = []
        if response.text:
            blocks.append(_Block("text", text=response.text))
        for call in response.tool_calls:
            blocks.append(_Block("tool_use", id=call.id, name=call.name, input=call.arguments))
        return _AnthropicResponse(blocks)


def _client(provider: str, cache):
    transport = _Transport(provider)
    if provider == "openai":
        inner = object.__new__(llm.OpenAIClient)
        inner.model = "model-from-env"
        inner._temperature = 0
    else:
        inner = object.__new__(llm.AnthropicClient)
        inner.model = "model-from-env"
        inner._temperature = 0
    inner._client = transport
    return llm.CachingClient(inner, cache), transport


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_the_flow_is_identical_whichever_provider_is_active(provider, conn, item, cfg,
                                                            cache, clean_books):
    client, transport = _client(provider, cache)
    assert client.provider == provider

    run = agent.run_seat(conn, "seat1", item["id"], client,
                         TASK.format(bank_txn=BANK_TXN), cfg=cfg)
    assert run.state == "PROPOSED"
    assert [c["name"] for c in run.tool_calls] == [
        "run_sql", "read_doc", "read_doc", "match_deposit", "propose"]

    personas.ScriptedController.load(cfg).review(conn, item["id"])
    result = gateway.post(conn, item["id"], cfg=cfg)

    postings = {}
    for line in result["entry"].splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == "USD":
            postings[parts[0]] = Decimal(parts[1].replace(",", ""))
    assert postings == JE3
    assert gateway.bean_check(cfg).ok

    # The adapter really did translate into provider shape.
    first = transport.requests[0]
    if provider == "openai":
        assert first["messages"][0]["role"] == "system"
        assert first["tools"][0]["type"] == "function"
        assert first["temperature"] == 0
    else:
        assert isinstance(first["system"], str)
        assert "input_schema" in first["tools"][0]
        assert first["max_tokens"] == llm.MAX_OUTPUT_TOKENS


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_a_model_that_rejects_temperature_is_retried_without_it(provider, cache):
    client, transport = _client(provider, cache)
    inner = client._inner
    original = transport.create
    calls = {"n": 0}

    def picky(**kwargs):
        calls["n"] += 1
        if "temperature" in kwargs:
            raise RuntimeError("400 Unsupported parameter: 'temperature' is not supported")
        return original(**kwargs)

    transport.create = picky
    response = inner.complete("sys", [{"role": "user", "content": "go"}], [])
    assert calls["n"] == 2                    # tried with, retried without
    assert inner._temperature is None         # and stops sending it
    assert response.tool_calls                # the turn still produced its tool call
