"""Provider-neutral LLM layer.

One canonical message/tool shape; adapters for OpenAI tool calling and
Anthropic tool use. LLM_PROVIDER picks the active provider; model ids come
only from LLM_MODEL / LLM_MODEL_FAST in .env and are never hardcoded.

Responses are cached in the desk DB, keyed by a hash of
(provider, model, system, messages, tools), so reruns are repeatable and cheap.

Canonical message shapes
------------------------
{"role": "user",      "content": "..."}
{"role": "assistant", "content": "...", "tool_calls": [{"id", "name", "arguments": {...}}]}
{"role": "tool",      "tool_call_id": "...", "name": "...", "content": "..."}

Canonical tool shape
--------------------
{"name": "...", "description": "...", "parameters": {<JSON schema>}}
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from .config import CFG, Config
from .db import now

MAX_OUTPUT_TOKENS = 8192


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class LLMResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "tool_calls": [c.to_dict() for c in self.tool_calls]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMResponse":
        return cls(
            text=data.get("text") or "",
            tool_calls=tuple(
                ToolCall(c["id"], c["name"], c.get("arguments") or {})
                for c in data.get("tool_calls") or []
            ),
        )

    def as_assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            message["tool_calls"] = [c.to_dict() for c in self.tool_calls]
        return message


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(
        self,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> LLMResponse: ...


def request_hash(
    provider: str,
    model: str,
    system: str,
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]],
) -> str:
    blob = json.dumps(
        {"provider": provider, "model": model, "system": system,
         "messages": list(messages), "tools": list(tools)},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #

class OpenAIClient:
    """OpenAI chat completions with tool calling."""

    provider = "openai"

    def __init__(self, model: str, api_key: str, *, temperature: float | None = 0):
        if not model:
            raise LLMError("LLM_MODEL is empty; set it in .env (model ids are never hardcoded)")
        if not api_key:
            raise LLMError("OPENAI_API_KEY is empty; set it in .env")
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key)
        # Some models reject sampling parameters. Send temperature until the
        # API says no, then stop sending it and rely on the response cache.
        self._temperature = temperature

    @staticmethod
    def to_openai_tools(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["parameters"]}}
            for t in tools
        ]

    @staticmethod
    def to_openai_messages(system: str, messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for message in messages:
            role = message["role"]
            if role == "tool":
                out.append({"role": "tool", "tool_call_id": message["tool_call_id"],
                            "content": message["content"]})
            elif role == "assistant":
                entry: dict[str, Any] = {"role": "assistant",
                                         "content": message.get("content") or None}
                calls = message.get("tool_calls") or []
                if calls:
                    entry["tool_calls"] = [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"],
                                      "arguments": json.dumps(c.get("arguments") or {})}}
                        for c in calls
                    ]
                out.append(entry)
            else:
                out.append({"role": "user", "content": message["content"]})
        return out

    def complete(self, system, messages, tools) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.to_openai_messages(system, messages),
        }
        if tools:
            kwargs["tools"] = self.to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as error:  # noqa: BLE001 - narrow on the message, not the class
            if self._temperature is not None and "temperature" in str(error).lower():
                self._temperature = None
                kwargs.pop("temperature", None)
                response = self._client.chat.completions.create(**kwargs)
            else:
                raise

        choice = response.choices[0].message
        calls = tuple(
            ToolCall(call.id, call.function.name, json.loads(call.function.arguments or "{}"))
            for call in (choice.tool_calls or [])
        )
        return LLMResponse(text=choice.content or "", tool_calls=calls)


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #

class AnthropicClient:
    """Claude Messages API with tool use."""

    provider = "anthropic"

    def __init__(self, model: str, api_key: str, *, temperature: float | None = 0):
        if not model:
            raise LLMError("LLM_MODEL is empty; set it in .env (model ids are never hardcoded)")
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is empty; set it in .env")
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key)
        self._temperature = temperature

    def available_models(self) -> list[str]:
        return [m.id for m in self._client.models.list(limit=100)]

    def verify_model(self) -> str:
        """Check LLM_MODEL against the models list endpoint; never guess an id."""
        ids = self.available_models()
        if self.model not in ids:
            raise LLMError(
                f"LLM_MODEL={self.model!r} is not in the Anthropic models list. "
                f"Available: {', '.join(ids)}"
            )
        return self.model

    @staticmethod
    def to_anthropic_tools(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in tools
        ]

    @staticmethod
    def to_anthropic_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Canonical -> Claude. Consecutive tool results merge into one user turn."""
        out: list[dict[str, Any]] = []
        for message in messages:
            role = message["role"]
            if role == "tool":
                block = {"type": "tool_result", "tool_use_id": message["tool_call_id"],
                         "content": message["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"],
                                   "input": call.get("arguments") or {}})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            else:
                out.append({"role": "user", "content": message["content"]})
        return out

    def complete(self, system, messages, tools) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system,
            "messages": self.to_anthropic_messages(messages),
        }
        if tools:
            kwargs["tools"] = self.to_anthropic_tools(tools)
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as error:  # noqa: BLE001 - narrow on the message, not the class
            if self._temperature is not None and "temperature" in str(error).lower():
                self._temperature = None
                kwargs.pop("temperature", None)
                response = self._client.messages.create(**kwargs)
            else:
                raise

        text_parts, calls = [], []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input or {})))
        return LLMResponse(text="".join(text_parts), tool_calls=tuple(calls))


# --------------------------------------------------------------------------- #
# Fake client (tests and scripted runs - no network)
# --------------------------------------------------------------------------- #

class SegmentedFakeLLM:
    """Scripted client that hands each work item its own transcript.

    A real model decides for itself when a work item is finished. A scripted one
    has to be told, or it runs on into the next item's turns - so the driver asks
    for a named segment and gets a fresh FakeLLM holding only those turns.
    """

    provider = "fake"
    model = "scripted"

    def __init__(self, segment_fn):
        self._segment_fn = segment_fn
        self._current: "FakeLLM | None" = None
        self.used: list[str] = []

    def for_segment(self, name: str) -> "FakeLLM":
        self.used.append(name)
        self._current = FakeLLM(script=self._segment_fn(name))
        return self._current

    def complete(self, system, messages, tools) -> "LLMResponse":
        if self._current is None:
            raise LLMError(
                "no scripted segment selected; the driver must call for_segment() first"
            )
        return self._current.complete(system, messages, tools)


@dataclass
class FakeLLM:
    """Returns scripted responses in order. Records what it was asked."""

    script: list[LLMResponse]
    provider: str = "fake"
    model: str = "fake-model"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, system, messages, tools) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages),
                           "tools": [t["name"] for t in tools]})
        if not self.script:
            raise LLMError("FakeLLM script exhausted: the agent asked for one more turn")
        return self.script.pop(0)


# --------------------------------------------------------------------------- #
# Caching wrapper
# --------------------------------------------------------------------------- #

class CachingClient:
    """Wraps any client and memoises responses in the cache DB (.cache/llm_cache.db)."""

    def __init__(self, inner: LLMClient, conn: sqlite3.Connection):
        self._inner = inner
        self._conn = conn
        self.hits = 0
        self.misses = 0

    @property
    def provider(self) -> str:
        return self._inner.provider

    def for_ticket(self, seat: str, item: dict[str, Any]) -> "CachingClient":
        """A brain that routes by ticket (the Creao agents) is bound through the cache."""
        bind = getattr(self._inner, "for_ticket", None)
        return CachingClient(bind(seat, item), self._conn) if bind else self

    def for_segment(self, name: str) -> "CachingClient":
        chooser = getattr(self._inner, "for_segment", None)
        return CachingClient(chooser(name), self._conn) if chooser else self

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(self, system, messages, tools) -> LLMResponse:
        key = request_hash(self.provider, self.model, system, messages, tools)
        row = self._conn.execute(
            "SELECT response FROM llm_cache WHERE request_hash = ?", (key,)
        ).fetchone()
        if row is not None:
            self.hits += 1
            return LLMResponse.from_dict(json.loads(row[0]))

        self.misses += 1
        response = self._inner.complete(system, messages, tools)
        self._conn.execute(
            "INSERT OR REPLACE INTO llm_cache (request_hash, provider, model, response, created_at)"
            " VALUES (?,?,?,?,?)",
            (key, self.provider, self.model, json.dumps(response.to_dict()), now()),
        )
        return response


def build_client(
    conn: sqlite3.Connection | None = None,
    cfg: Config = CFG,
    *,
    fast: bool = False,
) -> CachingClient:
    """Construct the configured provider's client, wrapped in the response cache.

    `conn` is the cache connection. Pass None to open .cache/llm_cache.db.
    """
    from .db import cache_conn

    conn = conn if conn is not None else cache_conn(cfg)
    provider = cfg.provider
    if provider == "creao" and not fast:
        # The seats' brains are the Creao agents (desk/creao.py). A dry run answers from
        # the recorded transcripts and is never cached, so it can never stand in for a
        # real answer later.
        from . import creao

        client = creao.build(cfg)
        return client if cfg.creao_dry_run else CachingClient(client, conn)
    if provider == "creao" and fast:
        provider = cfg.fast_provider or ""
        if not provider:
            raise LLMError("the Creao agents are not a model; the small classification calls need "
                           "LLM_MODEL_FAST and an ANTHROPIC_API_KEY or OPENAI_API_KEY")
    model = cfg.model_fast if fast else cfg.model
    # --- the desk's own model-backed seats (kept for fallback and for the fast calls) ---
    if provider == "openai":
        inner: LLMClient = OpenAIClient(model, cfg.openai_api_key)
    elif provider == "anthropic":
        inner = AnthropicClient(model, cfg.anthropic_api_key)
        inner.verify_model()
    else:
        raise LLMError(f"LLM_PROVIDER={cfg.provider!r} is not supported (use creao, openai or anthropic)")
    return CachingClient(inner, conn)
