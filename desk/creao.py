"""The Creao agents as the seats' brains (brewline_agents_integration.md).

The desk keeps every tool, limit, log row and approval. A Creao agent only supplies the
judgement: given a ticket and the conversation so far, it answers with text and tool
calls. Each turn is one app run on agent.creao.ai (the API trigger, app-scoped `capi_`
key):

    POST /api/v1/apps/{appId}/runs      {"inputs": {<the agent's form fields>}}
    GET  /api/v1/apps/{appId}/runs/{id}  until status is completed | failed | cancelled

The agent's tool calls come back inside `result.text` as JSON objects
`{"name": ..., "arguments": {...}}`. The desk runs them through its own registry (the
seat's jurisdiction, can(), the guard, the log) and sends the results back in the next
turn's context field. Every turn is self-contained: the whole transcript rides in the
form field, so the desk, not the platform, is the system of record.

Tickets route to agents by kind, not by seat: the Creao "stock & customers" agent owns the
refund split that the desk's seat2 works, and the "sourcing" agent has no ticket in the
refund flow yet (brewline_agents_integration.md §8).

`DryRunTransport` records every request the desk would send and answers from the
recorded transcripts, so the whole path (routing, form fields, the context, the parser,
the tool loop) runs with no network and no credit. `CREAO_DRY_RUN=1` selects it.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

from .config import CFG, Config
from .llm import LLMError, LLMResponse, ToolCall


@dataclass(frozen=True)
class Agent:
    key: str                      # tariffs | sourcing | stock (the config key)
    app_id: str
    name: str
    kinds: tuple[str, ...]        # the ticket_kind select on the agent's form
    form: str                     # which form fields it takes


AGENTS: dict[str, Agent] = {
    "tariffs": Agent("tariffs", "876e3e03-3bf3-42e3-9c1f-6d49288f3c6e", "Tariffs & Refunds (Brewline)",
                     ("WI-REFUND-DEPOSIT", "WI-FORECAST-CORRECTION", "WI-TARIFF-INCREASE",
                      "WI-DUTY-RECONCILIATION", "WI-ONBOARD-POLICY", "WI-ONBOARD-REVIEW"), "tariffs"),
    "sourcing": Agent("sourcing", "7cdf1832-18f9-413f-9f3f-3e6788fe9e8d", "Sourcing analyst - Brewline Tariff Desk",
                      ("WI-SOURCING-REVIEW", "WI-SOURCING-WATCH"), "sourcing"),
    "stock": Agent("stock", "d96d0e79-1492-43ec-8ce6-0e1a1aa73a6c", "stock-customers",
                   ("WI-REFUND-SPLIT", "WI-CUST-001-CREDIT", "WI-CUST-002-QUESTION", "WI-CUST-002-CLOSE",
                    "WI-CUST-PASSTHROUGH", "WI-ONBOARD-POLICY"), "stock"),
}

# The onboarding rules the stock & customers agent learns; the rest go to tariffs.
STOCK_ONBOARDING = ("POL-L2", "POL-L3")
SEAT_DEFAULT = {"seat1": "tariffs", "seat2": "stock", "seat3": "stock"}


def agent_for(kind: str, seat: str | None = None, payload: dict[str, Any] | None = None) -> Agent:
    """Which Creao agent owns a ticket (brewline_agents_integration.md §8)."""
    payload = payload or {}
    if kind.startswith("WI-SOURCING"):
        return AGENTS["sourcing"]
    if kind == "WI-ONBOARD-POLICY":
        return AGENTS["stock"] if payload.get("onboarding") in STOCK_ONBOARDING else AGENTS["tariffs"]
    if kind.startswith("WI-CUST-") or kind == "WI-REFUND-SPLIT":
        return AGENTS["stock"]
    if kind in AGENTS["tariffs"].kinds:
        return AGENTS["tariffs"]
    return AGENTS[SEAT_DEFAULT.get(seat or "", "tariffs")]


def select_kind(agent: Agent, kind: str) -> str:
    """The value for the agent's ticket_kind select: the kind itself when the form lists
    it, else the nearest listed kind (the real kind is always in the context text)."""
    if kind in agent.kinds:
        return kind
    if agent.key == "stock":
        if kind.endswith("-CREDIT"):
            return "WI-CUST-001-CREDIT"
        if kind.endswith("-QUESTION"):
            return "WI-CUST-002-QUESTION"
        if kind.endswith("-CLOSE"):
            return "WI-CUST-002-CLOSE"
        return "WI-REFUND-SPLIT"
    if agent.key == "sourcing":
        return "WI-SOURCING-REVIEW"
    return "WI-DUTY-RECONCILIATION"


def form_inputs(agent: Agent, kind: str, item_id: str, context: str) -> dict[str, str]:
    """The agent's form fields for one turn (brewline_agents_integration.md §2)."""
    selected = select_kind(agent, kind)
    if agent.form == "tariffs":
        return {"ticket_kind": selected, "ticket_id": item_id, "context": context}
    if agent.form == "sourcing":
        return {"ticket_kind": selected, "ticket": context}
    return {"ticket": context, "ticket_kind": selected}


# --------------------------------------------------------------------------- #
# the context field: the whole conversation, rendered as text
# --------------------------------------------------------------------------- #

CALL_RULES = """\
HOW TO CALL A TOOL. The desk runs every tool for you and sends the result back in this
field on the next turn. To call a tool, reply with one JSON object per call, on its own
line, exactly in this shape and nothing else inside the braces:
  {"name": "<tool name>", "arguments": {<the tool's arguments>}}
You may send several objects in one reply. Any prose outside the braces is kept as your
note to the record. When you have finished (you have called propose, ask or
propose_policy and nothing else is needed), reply in prose only, with no JSON. Use only
the tools listed below, with exactly these argument names; a tool outside the list is
refused and logged."""


def _tool_lines(tools: Sequence[dict[str, Any]]) -> str:
    lines = []
    for t in tools:
        params = t.get("parameters") or {}
        props = params.get("properties") or {}
        required = set(params.get("required") or [])
        args = ", ".join(f"{n}{'' if n in required else '?'}" for n in props)
        lines.append(f"  - {t['name']}({args}): {str(t.get('description') or '').strip()[:300]}")
    return "\n".join(lines) or "  (none)"


def render_context(system: str, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]],
                   *, kind: str, item_id: str) -> str:
    """One turn's context: the desk's rules, the tools, the task and everything since."""
    parts = [f"TICKET {item_id} ({kind}) at Brewline Equipment Co.'s tariff desk.",
             "", "DESK RULES (code enforces them; read them once):", system.strip(), "",
             CALL_RULES, "", "TOOLS YOU MAY CALL ON THIS TICKET:", _tool_lines(tools), ""]
    task = messages[0]["content"] if messages else ""
    parts += ["=== TASK ===", str(task).strip(), ""]
    if len(messages) > 1:
        parts.append("=== THE CONVERSATION SO FAR (oldest first) ===")
        for m in messages[1:]:
            role = m.get("role")
            if role == "assistant":
                if m.get("content"):
                    parts.append(f"YOU SAID: {m['content']}")
                for call in m.get("tool_calls") or []:
                    parts.append(f"YOU CALLED {call['name']} (call {call['id']}): "
                                 f"{json.dumps(call.get('arguments') or {}, default=str)}")
            elif role == "tool":
                parts.append(f"TOOL RESULT for {m.get('name')} (call {m.get('tool_call_id')}):\n{m.get('content')}")
            else:
                parts.append(f"THE DESK SAID: {m.get('content')}")
            parts.append("")
        parts.append("=== YOUR NEXT TURN ===")
        parts.append("Continue from the results above. Call the next tool(s), or finish in prose.")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# the agent's answer: text, with tool calls as JSON objects
# --------------------------------------------------------------------------- #

_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


def _json_objects(text: str) -> list[tuple[int, int, Any]]:
    """Every top-level balanced {...} in the text that parses as JSON, with its span."""
    found = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, j, in_str, esc = 0, i, False, False
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            break
        candidate = text[i:j + 1]
        try:
            found.append((i, j + 1, json.loads(candidate)))
            i = j + 1
        except json.JSONDecodeError:
            i += 1
    return found


def _as_calls(value: Any) -> list[dict[str, Any]]:
    """A parsed JSON value -> the tool calls it carries, in the desk's shape."""
    calls: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("tool_calls"), list):
            for v in value["tool_calls"]:
                calls += _as_calls(v)
            return calls
        name = value.get("name") or value.get("tool") or value.get("tool_name")
        args = value.get("arguments", value.get("args", value.get("input", value.get("parameters"))))
        if isinstance(name, str) and name and (args is None or isinstance(args, dict)):
            calls.append({"name": name, "arguments": dict(args or {})})
    elif isinstance(value, list):
        for v in value:
            calls += _as_calls(v)
    return calls


def parse_output(text: str) -> LLMResponse:
    """The agent's reply -> text plus tool calls. JSON objects that look like tool calls
    become calls (fenced or bare); everything else stays as the agent's note."""
    text = unwrap(text or "")
    body = _FENCE.sub(lambda m: m.group(1), text)
    calls: list[ToolCall] = []
    keep: list[str] = []
    cursor = 0
    for start, end, value in _json_objects(body):
        found = _as_calls(value)
        if not found:
            continue
        keep.append(body[cursor:start])
        cursor = end
        for c in found:
            calls.append(ToolCall(f"creao_{uuid.uuid4().hex[:8]}", c["name"], c["arguments"]))
    keep.append(body[cursor:])
    note = re.sub(r"\n{3,}", "\n\n", "".join(keep)).strip()
    return LLMResponse(text=note, tool_calls=tuple(calls))


def render_response(response: LLMResponse) -> str:
    """The inverse of parse_output, for dry runs: a recorded answer as Creao text."""
    lines = [response.text] if response.text else []
    for call in response.tool_calls:
        lines.append(json.dumps({"name": call.name, "arguments": call.arguments}, default=str))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# transports: the real API, and the dry run
# --------------------------------------------------------------------------- #

class Transport(Protocol):
    def run(self, agent: Agent, inputs: dict[str, str], key: str, *, segment: str | None,
            turn: int) -> str: ...


class HttpTransport:
    """agent.creao.ai API trigger: create a run, poll it, return result.text."""

    def __init__(self, base_url: str, *, timeout_seconds: int = 300, poll_seconds: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.last_run: dict[str, Any] | None = None

    def run(self, agent: Agent, inputs: dict[str, str], key: str, *, segment=None, turn=0) -> str:
        import httpx

        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/api/v1/apps/{agent.app_id}/runs"
        with httpx.Client(timeout=60) as http:
            try:
                created = http.post(url, json={"inputs": inputs}, headers=headers)
            except httpx.HTTPError as error:
                raise LLMError(f"Creao {agent.name}: could not start a run: {error}") from error
            if created.status_code == 429:
                retry_after = created.headers.get("Retry-After")
                try:
                    time.sleep(max(5.0, float(retry_after)) if retry_after else 5.0)
                except ValueError:
                    time.sleep(5.0)
                created = http.post(url, json={"inputs": inputs}, headers=headers)
            if created.status_code >= 400:
                raise LLMError(f"Creao {agent.name}: HTTP {created.status_code} starting a run: "
                               f"{created.text[:300]}")
            run = created.json()
            run_id = run.get("id") or run.get("runId") or run.get("run_id")
            if not run_id:
                raise LLMError(f"Creao {agent.name}: no run id in {str(run)[:200]}")
            deadline = time.monotonic() + self.timeout_seconds
            wait = self.poll_seconds
            while True:
                # Polling is rate limited: back off gently, and treat 429 as "wait longer",
                # honouring Retry-After when the platform sends it.
                time.sleep(wait)
                wait = min(wait * 1.5, 10.0)
                polled = http.get(f"{url}/{run_id}", headers=headers)
                if polled.status_code == 429:
                    retry_after = polled.headers.get("Retry-After")
                    try:
                        wait = max(wait, float(retry_after)) if retry_after else max(wait, 5.0)
                    except ValueError:
                        wait = max(wait, 5.0)
                    if time.monotonic() > deadline:
                        raise LLMError(f"Creao {agent.name}: rate limited while polling run {run_id} "
                                       f"for {self.timeout_seconds}s")
                    continue
                if polled.status_code >= 400:
                    raise LLMError(f"Creao {agent.name}: HTTP {polled.status_code} polling run {run_id}: "
                                   f"{polled.text[:300]}")
                data = polled.json()
                status = str(data.get("status") or "").lower()
                if status in ("completed", "succeeded", "success"):
                    self.last_run = data
                    return result_text(data)
                if status in ("failed", "cancelled", "canceled", "error"):
                    self.last_run = data
                    raise LLMError(f"Creao {agent.name}: run {run_id} {status}: "
                                   f"{data.get('errorCode') or data.get('error') or ''}".strip())
                if time.monotonic() > deadline:
                    raise LLMError(f"Creao {agent.name}: run {run_id} still {status or 'pending'} "
                                   f"after {self.timeout_seconds}s")


def unwrap(text: str) -> str:
    """The platform sometimes hands the reply as `{"response": "..."}`; take the inside."""
    stripped = (text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return text
        if isinstance(value, dict):
            for k in ("response", "text", "output", "message", "content"):
                if isinstance(value.get(k), str) and value[k]:
                    return value[k]
    return text


def result_text(data: dict[str, Any]) -> str:
    """The agent's final text from a polled run, whichever field the platform used. The
    app-run API fills `result.text` with a short, sometimes truncated summary and
    `result.chatText` with the whole reply, so the longest candidate wins."""
    result = data.get("result")
    candidates: list[str] = []
    if isinstance(result, dict):
        for k in ("chatText", "chat_text", "text", "output", "content"):
            if isinstance(result.get(k), str) and result[k]:
                candidates.append(unwrap(result[k]))
        blocks = result.get("blocks")
        if isinstance(blocks, list):
            texts = [b.get("text") for b in blocks if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
            if texts:
                candidates.append("\n".join(texts))
    elif isinstance(result, str) and result:
        candidates.append(unwrap(result))
    for k in ("chatText", "text", "output", "content"):
        if isinstance(data.get(k), str) and data[k]:
            candidates.append(unwrap(data[k]))
    return max(candidates, key=len) if candidates else ""


@dataclass
class DryRunTransport:
    """No network. Records what the desk would send; answers from recorded transcripts
    (`script(segment)` -> the desk's scripted LLMResponses) or a fixed note."""

    script: Callable[[str], list[LLMResponse]] | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)
    _queues: dict[str, list[LLMResponse]] = field(default_factory=dict)

    def run(self, agent: Agent, inputs: dict[str, str], key: str, *, segment=None, turn=0) -> str:
        self.requests.append({"agent": agent.key, "app_id": agent.app_id, "inputs": dict(inputs),
                              "key_set": bool(key), "segment": segment, "turn": turn})
        if self.script and segment:
            queue = self._queues.setdefault(segment, list(self.script(segment)))
            if not queue:
                raise LLMError(f"dry run: the recorded segment {segment!r} has no more turns")
            return render_response(queue.pop(0))
        return "Dry run: no agent was called. The desk routed this ticket and built its context."


# --------------------------------------------------------------------------- #
# the client the harness talks to
# --------------------------------------------------------------------------- #

class CreaoClient:
    """An LLMClient whose brain is the Creao agent that owns the ticket."""

    provider = "creao"

    def __init__(self, cfg: Config, transport: Transport, *, segment: str | None = None,
                 ticket: tuple[str, dict[str, Any]] | None = None):
        self.cfg = cfg
        self.transport = transport
        self.segment = segment
        self.ticket = ticket            # (seat, work item) once the harness binds one
        self.model = f"creao:{self.agent().key}" if ticket else "creao-apps"

    def for_segment(self, name: str) -> "CreaoClient":
        return CreaoClient(self.cfg, self.transport, segment=name, ticket=self.ticket)

    def for_ticket(self, seat: str, item: dict[str, Any]) -> "CreaoClient":
        return CreaoClient(self.cfg, self.transport, segment=self.segment, ticket=(seat, item))

    def agent(self) -> Agent:
        seat, item = self.ticket or ("seat1", {})
        return agent_for(item.get("kind") or "", seat, item.get("payload") or {})

    def complete(self, system, messages, tools) -> LLMResponse:
        seat, item = self.ticket or ("seat1", {})
        agent = self.agent()
        key = self.cfg.creao_keys.get(agent.key, "")
        if not key and not isinstance(self.transport, DryRunTransport):
            raise LLMError(f"no API key for the Creao agent {agent.name!r}; set it in .env")
        kind = item.get("kind") or "(no ticket)"
        item_id = item.get("id") or "(no ticket)"
        context = render_context(system, messages, tools, kind=kind, item_id=item_id)
        inputs = form_inputs(agent, kind, item_id, context)
        text = self.transport.run(agent, inputs, key, segment=self.segment, turn=len(messages))
        return parse_output(text)


def build(cfg: Config = CFG, *, dry_run: bool | None = None) -> CreaoClient:
    dry = cfg.creao_dry_run if dry_run is None else dry_run
    if dry:
        from .scripted import segment

        return CreaoClient(cfg, DryRunTransport(script=segment))
    return CreaoClient(cfg, HttpTransport(cfg.creao_base_url, timeout_seconds=cfg.creao_timeout))
