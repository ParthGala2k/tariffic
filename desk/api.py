"""HTTP API over the desk: the base data, the work, the people's actions, and
the live feed the UI draws its "thinking" view from.

Start it with `python -m desk serve`. Everything a human can do here goes
through the same can() and transition() checks as the CLI and the scripted
personas: there is no admin door. Reads of the systems of record go through
the golden tables, so the API can no more read data/truth than a seat can.

Frontend hooks
  GET  /status                     seats' modes, counts, who is waiting on whom
  GET  /events?after=N             the audit feed, enriched with mode + label
  GET  /stream                     the same, as Server-Sent Events
  GET  /work-items/{id}/brief      what the human needs to read before deciding
  POST /work-items/{id}/approve    | /reject | /send-back | /challenge | /resume
  POST /inbox                      put a document or a link in front of the desk
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Iterator

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from pathlib import Path

from . import __main__ as cli
from . import activity, brief as brief_mod, challenge as challenge_mod, db, gateway, humans, sync
from . import creao as creao_mod, inbox as inbox_mod, invariants, llm as llm_mod, plain as plain_mod, policies, precedents, scenario as scenario_mod, score
from .config import CFG, Config
from .evidence import EvidenceRejected
from .guard import Refused, check_doc_path, check_sql
from .perms import Denied, ROLES
from .runs import MANAGER, RunBusy
from .tools import REGISTRY, SEAT_TOOLS, Ctx, dispatch
from .workflow import children, get_item, history

app = FastAPI(title="Brewline Tariff Desk", version="0.1",
              description=__doc__ or "", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.state.cfg = CFG
app.state.scenario = None          # {"name", "spec", "cfg", "report"} while a sandbox is active
SCENARIOS_DIR = CFG.root / "scenarios"


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #

def _cfg(request: Request) -> Config:
    return request.app.state.cfg


def _recorded_run(request: Request) -> bool:
    active = request.app.state.scenario
    run = MANAGER.get(active["run_id"]) if active and active.get("run_id") else None
    return bool(run and run.llm == "scripted")


def _conn(cfg: Config) -> sqlite3.Connection:
    return db.desk_conn(cfg)


_BEAN_CACHE: dict[str, tuple[tuple, Any]] = {}


def _bean_check_cached(cfg: Config):
    """bean-check is a subprocess over the whole ledger; run it only when a book file changed."""
    books = cfg.beancount_file.parent
    stamp = tuple(sorted((p.as_posix(), p.stat().st_mtime_ns) for p in books.rglob("*.beancount")))
    key = str(cfg.beancount_file)
    cached = _BEAN_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    result = gateway.bean_check(cfg)
    _BEAN_CACHE[key] = (stamp, result)
    return result


def _llm_ready(cfg: Config) -> bool:
    return cfg.live_ready


def _live_client(cfg: Config, *, fast: bool = False):
    if not _llm_ready(cfg):
        raise HTTPException(
            503, "no live model is configured: set LLM_PROVIDER, LLM_MODEL and the provider's "
                 "API key in .env, or use scripted mode")
    try:
        return llm_mod.build_client(db.cache_conn(cfg), cfg, fast=fast)
    except llm_mod.LLMError as error:
        raise HTTPException(503, str(error)) from error


def _llm_usable(cfg: Config) -> tuple[bool, str]:
    """A key can authenticate and still be refused (no credit). One tiny call settles it."""
    if not _llm_ready(cfg):
        return False, "no model configured"
    if cfg.provider == "creao":
        # An agent run costs credits and there is no free ping; the keys being present is
        # the check. A dead key fails loudly on the first turn instead.
        return True, ""
    try:
        client = llm_mod.build_client(db.cache_conn(cfg), cfg, fast=bool(cfg.model_fast))
        raw = getattr(client, "_inner", client)      # the cache would answer for a dead key
        raw.complete("Reply with the single word OK.", [{"role": "user", "content": "OK?"}], [])
        return True, ""
    except Exception as error:  # noqa: BLE001 - the reason is shown, not raised
        return False, f"{type(error).__name__}: {str(error)[:160]}"


def _fast_client_or_none(cfg: Config):
    if not cfg.fast_provider:
        return None
    try:
        return llm_mod.build_client(db.cache_conn(cfg), cfg, fast=True)
    except llm_mod.LLMError:
        return None


@app.exception_handler(Denied)
def _denied(_request, error: Denied):
    return _error(403, "denied", str(error), actor=error.actor, action=error.action,
                  subject=error.subject)


@app.exception_handler(Refused)
def _refused(_request, error: Refused):
    return _error(403, "refused", str(error), target=error.target)


@app.exception_handler(EvidenceRejected)
def _evidence_rejected(_request, error: EvidenceRejected):
    return _error(422, "evidence_rejected", error.reason, checks=error.checks)


@app.exception_handler(humans.NotFound)
def _not_found(_request, error: humans.NotFound):
    return _error(404, "not_found", str(error))


@app.exception_handler(policies.PolicyError)
def _policy_error(_request, error: policies.PolicyError):
    return _error(400, "policy_error", str(error))


@app.exception_handler(gateway.PostFailed)
def _post_failed(_request, error: gateway.PostFailed):
    return _error(409, "post_failed", str(error))


@app.exception_handler(RunBusy)
def _busy(_request, error: RunBusy):
    return _error(409, "busy", str(error))


@app.exception_handler(inbox_mod.InboxError)
def _inbox_error(_request, error: inbox_mod.InboxError):
    return _error(400, "inbox_error", str(error))


@app.exception_handler(ValueError)
def _value_error(_request, error: ValueError):
    return _error(400, "invalid", str(error))


def _error(status: int, kind: str, detail: str, **extra):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"error": kind, "detail": detail, **extra})


# --------------------------------------------------------------------------- #
# health, status, feed
# --------------------------------------------------------------------------- #

@app.get("/health")
def health(request: Request):
    cfg = _cfg(request)
    check = _bean_check_cached(cfg)
    return {
        "ok": check.ok, "bean_check": check.ok, "bean_check_output": check.output[:500],
        "desk_db": cfg.desk_db.exists(), "erp_db": cfg.erp_db.exists(),
        "llm": {"configured": _llm_ready(cfg), "provider": cfg.provider,
                "model": cfg.model or None, "model_fast": cfg.model_fast or None,
                "dry_run": cfg.creao_dry_run if cfg.provider == "creao" else None,
                "agents": ([{"agent": a.key, "name": a.name, "app_id": a.app_id,
                             "key_set": bool(cfg.creao_keys.get(a.key))}
                            for a in creao_mod.AGENTS.values()]
                           if cfg.provider == "creao" else None)},
        "active_run": (MANAGER.active().as_dict() | {"lines": None}) if MANAGER.active() else None,
    }


@app.get("/status")
def status(request: Request):
    conn = _conn(_cfg(request))
    try:
        snap = activity.snapshot(conn)
    finally:
        conn.close()
    active = MANAGER.active()
    snap["run"] = (active.as_dict() | {"lines": None}) if active else None
    return snap


@app.get("/events")
def events(request: Request, after: int = 0, limit: int = 200, subject: str | None = None,
           actor: str | None = None, permission_checks: bool = False):
    conn = _conn(_cfg(request))
    try:
        rows = activity.feed(conn, after=after, limit=limit, subject=subject, actor=actor,
                             include_permission_checks=permission_checks)
        last = activity.last_event_id(conn)
    finally:
        conn.close()
    return {"events": rows, "last_event_id": last, "count": len(rows)}


@app.get("/stream")
def stream(request: Request, after: int = 0, subject: str | None = None,
           permission_checks: bool = False, poll_seconds: float = 0.5):
    """Server-Sent Events: one `event: desk` per audit row, plus heartbeats."""
    def generate() -> Iterator[str]:
        cursor = after
        quiet = 0
        yield f"event: hello\ndata: {json.dumps({'after': cursor})}\n\n"
        while True:
            conn = _conn(request.app.state.cfg)      # follows a scenario activation
            try:
                rows = activity.feed(conn, after=cursor, limit=200, subject=subject,
                                     include_permission_checks=permission_checks)
                seats = activity.seat_status(conn) if rows else None
            finally:
                conn.close()
            for row in rows:
                cursor = row["event_id"]
                yield f"id: {cursor}\nevent: desk\ndata: {json.dumps(row, default=str)}\n\n"
            if rows:
                yield f"event: seats\ndata: {json.dumps(seats, default=str)}\n\n"
                quiet = 0
            else:
                quiet += 1
                if quiet % max(1, int(10 / max(poll_seconds, 0.1))) == 0:
                    yield f"event: heartbeat\ndata: {json.dumps({'last_event_id': cursor})}\n\n"
            time.sleep(max(poll_seconds, 0.1))

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- #
# work items
# --------------------------------------------------------------------------- #

@app.get("/work-items")
def list_work_items(request: Request, state: str | None = None, kind: str | None = None):
    conn = _conn(_cfg(request))
    try:
        sql, params = "SELECT * FROM work_items WHERE 1=1", []
        if state:
            sql += " AND state = ?"
            params.append(state)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        rows = conn.execute(sql + " ORDER BY id", params).fetchall()
        from .workflow import row_to_dict
        items = [row_to_dict(r) for r in rows]
        titles = brief_mod.KIND_TITLES
        for item in items:
            item["title"] = titles.get(item["kind"]) or item["kind"]
    finally:
        conn.close()
    return {"work_items": items, "count": len(items)}


@app.get("/work-items/{item_id}")
def work_item(item_id: str, request: Request):
    conn = _conn(_cfg(request))
    try:
        item = get_item(conn, item_id)
        if item is None:
            raise HTTPException(404, f"no work item {item_id}")
        item["title"] = brief_mod.KIND_TITLES.get(item["kind"]) or item["kind"]
        item["children"] = children(conn, item_id)
        item["questions"] = [dict(r) for r in conn.execute(
            "SELECT * FROM questions WHERE work_item_id = ? ORDER BY created_at", (item_id,))]
        item["challenges"] = [dict(r) for r in conn.execute(
            "SELECT * FROM challenges WHERE work_item_id = ? ORDER BY created_at", (item_id,))]
    finally:
        conn.close()
    return item


@app.get("/work-items/{item_id}/history")
def work_item_history(item_id: str, request: Request, permission_checks: bool = True):
    conn = _conn(_cfg(request))
    try:
        if get_item(conn, item_id) is None:
            raise HTTPException(404, f"no work item {item_id}")
        names = activity.names_for(conn)
        rows = [activity.enrich(r, names) for r in history(conn, item_id)]
    finally:
        conn.close()
    if not permission_checks:
        rows = [r for r in rows if not r["noise"]]
    return {"work_item": item_id, "events": rows, "count": len(rows),
            "refused": sum(1 for r in rows if not r["allowed"])}


@app.get("/work-items/{item_id}/brief")
def work_item_brief(item_id: str, request: Request):
    cfg = _cfg(request)
    conn = _conn(cfg)
    try:
        result = brief_mod.brief(conn, item_id, cfg)
        if result is not None:
            result["plain"] = plain_mod.plain(conn, cfg, result)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(404, f"no work item {item_id}")
    return result


class Decision(BaseModel):
    actor: str = Field("human_cfo", description="who is deciding: human_cfo (or human_controller in a recorded run)")
    reason: str = ""
    post: bool = Field(True, description="on approval, let the gateway post the entry")


@app.post("/work-items/{item_id}/approve")
def approve(item_id: str, request: Request, body: Decision = Body(default=Decision())):
    cfg = _cfg(request)
    conn = _conn(cfg)
    try:
        result = humans.approve(conn, item_id, body.actor, body.reason, post=body.post, cfg=cfg)
    finally:
        conn.close()
    return result


@app.post("/work-items/{item_id}/reject")
def reject(item_id: str, request: Request, body: Decision):
    conn = _conn(_cfg(request))
    try:
        return humans.reject(conn, item_id, body.actor, body.reason)
    finally:
        conn.close()


@app.post("/work-items/{item_id}/send-back")
def send_back(item_id: str, request: Request, body: Decision):
    conn = _conn(_cfg(request))
    try:
        return humans.send_back(conn, item_id, body.actor, body.reason)
    finally:
        conn.close()


class Challenge(BaseModel):
    actor: str = Field(..., description="who is pushing back: human_controller, human_cfo, ...")
    text: str = Field(..., description="the argument")
    document: str | None = Field(None, description="an inbox document path that backs it, if any")
    verify_with_seat: bool = Field(
        True, description="if it is evidence and a live model is configured, let the seat "
                          "read it and attempt revise_proposal")


@app.post("/work-items/{item_id}/challenge")
def challenge(item_id: str, request: Request, body: Challenge):
    """Push back on a decision. Opinion and authority are logged and change
    nothing; a document is checked and, with a live model, handed to the seat."""
    cfg = _cfg(request)
    live = _llm_ready(cfg) and not _recorded_run(request)
    conn = _conn(cfg)
    try:
        item = get_item(conn, item_id)
        if item is None:
            raise HTTPException(404, f"no work item {item_id}")
        fast = _fast_client_or_none(cfg) if live else None
        classifier_note = None
        try:
            outcome = challenge_mod.raise_challenge(
                conn, item_id, raised_by=body.actor, text=body.text, document=body.document,
                llm=fast, cfg=cfg)
        except Exception as error:  # noqa: BLE001 - a provider outage must not block pushback
            if fast is None:
                raise
            classifier_note = f"live classifier unavailable ({type(error).__name__}); classified offline"
            outcome = challenge_mod.raise_challenge(
                conn, item_id, raised_by=body.actor, text=body.text, document=body.document,
                llm=None, cfg=cfg)
        result = outcome.as_dict()
        result["classifier_note"] = classifier_note
        result["seat_verification"] = None
        result["reply"] = None
        verifying = (outcome.classification == challenge_mod.EVIDENCE and body.document
                     and body.verify_with_seat)
        if not verifying:
            # Opinion, authority, or evidence with nothing to check: the seat answers the
            # person with facts; code has already decided that nothing changes.
            if live:
                try:
                    result["reply"] = challenge_mod.reply(
                        conn, outcome.challenge_id, seat_llm=_live_client(cfg), cfg=cfg)
                except Exception as error:  # noqa: BLE001 - the person still gets an answer
                    result["reply"] = challenge_mod.fallback_reply(
                        conn, outcome.challenge_id, cfg=cfg,
                        note=f"the live model failed: {type(error).__name__}")
            else:
                result["reply"] = challenge_mod.fallback_reply(conn, outcome.challenge_id, cfg=cfg)
        if verifying:
            if not live:
                result["seat_verification"] = {
                    "ran": False,
                    "note": "the document is admitted; no live model is configured, so the "
                            "seat did not read it. Set LLM_MODEL and a key in .env, or call "
                            "revise_proposal through a seat run."}
            else:
                from . import agent

                seat = item["seat"] or "seat3"
                before = (item.get("payload") or {}).get("revision")
                try:
                    run = agent.run_seat(
                        conn, seat, item_id, _live_client(cfg),
                        cli.CHALLENGE_TASK.format(work_item=item_id, kind=item["kind"],
                                                  document=body.document, text=body.text),
                        cfg=cfg)
                except HTTPException:
                    raise
                except Exception as error:  # noqa: BLE001 - report the outage, keep the challenge
                    result["seat_verification"] = {
                        "ran": False,
                        "note": f"the document is admitted but the live model failed: "
                                f"{type(error).__name__}: {str(error)[:200]}"}
                    result["counters"] = challenge_mod.counters(conn)
                    return result
                after = get_item(conn, item_id)
                changed = (after.get("payload") or {}).get("revision") != before
                result["changed"] = changed
                result["reply"] = challenge_mod.store_reply(
                    conn, outcome.challenge_id, run.final_text, agent.actor_for(seat))
                result["seat_verification"] = {
                    "ran": True, "seat": seat, "turns": run.turns,
                    "tool_calls": [c["name"] + ("" if c["ok"] else "[refused]")
                                   for c in run.tool_calls],
                    "revised_amount": (after.get("payload") or {}).get("revised_amount"),
                    "correcting_work_item": (after.get("payload") or {}).get("correcting_work_item"),
                    "seat_says": run.final_text[:600]}
        result["counters"] = challenge_mod.counters(conn)
    finally:
        conn.close()
    return result


@app.post("/work-items/{item_id}/resume")
def resume(item_id: str, request: Request):
    """Run the seat again after its question was answered (live model required)."""
    if _recorded_run(request):
        raise HTTPException(409, "Recorded replay progresses automatically; no live model will be called.")
    cfg = _cfg(request)
    conn = _conn(cfg)
    try:
        run = humans.resume(conn, item_id, _live_client(cfg), cfg=cfg)
        item = get_item(conn, item_id)
    finally:
        conn.close()
    return {"work_item": item, "turns": run.turns, "state": run.state,
            "tool_calls": [c["name"] + ("" if c["ok"] else "[refused]") for c in run.tool_calls],
            "seat_says": run.final_text[:600]}


# --------------------------------------------------------------------------- #
# questions, policies, precedents, challenges
# --------------------------------------------------------------------------- #

@app.get("/questions")
def questions(request: Request, state: str | None = None):
    conn = _conn(_cfg(request))
    try:
        sql, params = "SELECT * FROM questions", []
        if state:
            sql += " WHERE state = ?"
            params.append(state)
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY created_at", params)]
    finally:
        conn.close()
    return {"questions": rows, "count": len(rows)}


class Answer(BaseModel):
    actor: str = Field(..., description="human_cfo or human_lawyer")
    text: str
    document: str | None = Field(None, description="an inbox document, if one backs the answer")
    rule_proposal: dict[str, Any] | None = Field(
        None, description="a lawyer may propose a rule: {id, rule, scope, applies_to, kind}")


@app.post("/questions/{question_id}/answer")
def answer(question_id: str, request: Request, body: Answer):
    cfg = _cfg(request)
    conn = _conn(cfg)
    try:
        return humans.answer(conn, question_id, body.actor, body.text, document=body.document,
                             rule_proposal=body.rule_proposal, cfg=cfg)
    finally:
        conn.close()


@app.get("/policies")
def list_policies(request: Request, status: str | None = None):
    conn = _conn(_cfg(request))
    try:
        return {"policies": policies.listing(conn, status)}
    finally:
        conn.close()


class PolicyDecision(BaseModel):
    actor: str = "human_cfo"
    reason: str = ""


@app.post("/policies/{policy_ref}/approve")
def approve_policy(policy_ref: str, request: Request, body: PolicyDecision = Body(default=PolicyDecision())):
    conn = _conn(_cfg(request))
    try:
        return humans.approve_policy(conn, policy_ref, body.actor, body.reason)
    finally:
        conn.close()


@app.get("/precedents")
def list_precedents(request: Request, q: str = ""):
    conn = _conn(_cfg(request))
    try:
        found = precedents.search(conn, q)
    finally:
        conn.close()
    return {"precedents": found, "count": len(found)}


@app.get("/challenges")
def list_challenges(request: Request):
    conn = _conn(_cfg(request))
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM challenges ORDER BY created_at")]
        counters = challenge_mod.counters(conn)
    finally:
        conn.close()
    return {"challenges": rows, "counters": counters}


# --------------------------------------------------------------------------- #
# the base data: golden tables, books, documents, invariants, score
# --------------------------------------------------------------------------- #

@app.get("/golden")
def golden_tables(request: Request):
    conn = _conn(_cfg(request))
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'golden_%' ORDER BY name")]
        out = [{"table": n, "rows": conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]}
               for n in names]
    finally:
        conn.close()
    return {"tables": out}


@app.get("/golden/{table}")
def golden_rows(table: str, request: Request, limit: int = Query(200, le=1000), offset: int = 0):
    statement = check_sql(f"SELECT * FROM {table}")        # golden_* only, read-only
    conn = _conn(_cfg(request))
    try:
        rows = [dict(r) for r in conn.execute(f"{statement} LIMIT ? OFFSET ?", (limit, offset))]
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()
    return {"table": table, "rows": rows, "count": len(rows), "total": total}


class SqlQuery(BaseModel):
    sql: str
    limit: int = 200


@app.post("/golden/query")
def golden_query(request: Request, body: SqlQuery):
    """The same read-only, golden-only SELECT a seat is allowed."""
    from .tools import read

    ctx = Ctx(conn=None, actor="api", seat="api", cfg=_cfg(request))   # type: ignore[arg-type]
    return read.run_sql(ctx, body.sql, body.limit)


@app.get("/books/posted", response_class=PlainTextResponse)
def posted_book(request: Request):
    return _cfg(request).posted_file.read_text(encoding="utf-8")


@app.get("/books/balances")
def balances(request: Request):
    cfg = _cfg(request)
    bals = invariants.ledger_balances(cfg)
    check = _bean_check_cached(cfg)
    return {"balances": {k: f"{v:.2f}" for k, v in sorted(bals.items())},
            "bean_check": check.ok, "bean_check_output": check.output[:500]}


@app.get("/invariants")
def invariant_state(request: Request):
    cfg = _cfg(request)
    conn = _conn(cfg)
    try:
        found = invariants.evaluate(conn, cfg)
    finally:
        conn.close()
    return {"violations": [v.as_dict() for v in found], "count": len(found),
            "titles": invariants.TITLES}


@app.get("/documents")
def documents(request: Request):
    return {"documents": inbox_mod.list_documents(_cfg(request))}


@app.get("/documents/{path:path}", response_class=PlainTextResponse)
def document(path: str, request: Request):
    resolved = check_doc_path(path, _cfg(request))     # Refused -> 403; never data/truth
    if not resolved.is_file():
        raise HTTPException(404, f"{path} is not a document")
    return resolved.read_text(encoding="utf-8", errors="replace")


class Upload(BaseModel):
    title: str
    body: str | None = None
    url: str | None = Field(None, description="fetch this link and store its text")
    type: str = "uploaded_evidence"
    sender: str | None = None
    parties: list[str] | None = None
    date: str | None = Field(None, description="the document's own date, YYYY-MM-DD")
    relates_to: list[str] | None = Field(None, description="contract ids it bears on, e.g. [CON-002]")
    uploaded_by: str = "human_controller"
    work_item_id: str | None = None


@app.post("/inbox", status_code=201)
def upload(request: Request, body: Upload):
    """Put a document or a link in front of the desk as citable evidence."""
    cfg = _cfg(request)
    conn = _conn(cfg)
    try:
        return inbox_mod.add_document(
            cfg, title=body.title, body=body.body, url=body.url, doc_type=body.type,
            sender=body.sender, parties=body.parties, doc_date=body.date,
            relates_to=body.relates_to, uploaded_by=body.uploaded_by,
            work_item_id=body.work_item_id, conn=conn)
    finally:
        conn.close()


@app.get("/score")
def scorecard(request: Request, phase: int = 3):
    cfg = _cfg(request)
    conn = _conn(cfg)
    try:
        try:
            card = score.score(conn, cfg, phase=phase)
        except score.OutOfSync as problem:
            raise HTTPException(409, str(problem)) from problem
    finally:
        conn.close()
    return {"passed": card.passed, "counters": card.counters,
            "sections": {name: [line.__dict__ for line in lines]
                         for name, lines in card.sections.items()},
            "text": score.render(card)}


# --------------------------------------------------------------------------- #
# seats and tools: the jurisdiction map, and a read-only playground
# --------------------------------------------------------------------------- #

@app.get("/seats")
def seats(request: Request):
    from . import agent

    out = []
    for seat, names in SEAT_TOOLS.items():
        out.append({
            "seat": seat, "name": activity.SEAT_NAMES.get(seat, seat),
            "actor": f"ai_agent.{seat}", "role": agent.SEAT_ROLES.get(seat, ""),
            "tools": [{"name": n, "kind": REGISTRY[n].kind, "action": REGISTRY[n].action,
                       "description": REGISTRY[n].description} for n in names],
        })
    return {"seats": out, "roles": {role: sorted(actions) for role, actions in ROLES.items()},
            "forbidden_for_agents": ["post", "approve", "reject", "approve_policy"]}


class ToolCall(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    work_item_id: str | None = None


@app.post("/tools/{seat}/{tool}")
def call_tool(seat: str, tool: str, request: Request, body: ToolCall = Body(default=ToolCall())):
    """Run a read or compute tool exactly as the seat would, with the seat's
    permissions and the same audit row. Work tools (propose, ask, ...) are not
    exposed: those belong to the seat's own run."""
    if seat not in SEAT_TOOLS:
        raise HTTPException(404, f"no seat {seat}")
    registered = REGISTRY.get(tool)
    if registered is not None and registered.kind == "work":
        raise HTTPException(405, f"{tool} is a work tool; only a seat run may call it")
    conn = _conn(_cfg(request))
    try:
        ctx = Ctx(conn=conn, actor=f"ai_agent.{seat}", seat=seat,
                  work_item_id=body.work_item_id, cfg=_cfg(request))
        ok, content = dispatch(ctx, tool, body.arguments)
    finally:
        conn.close()
    try:
        parsed = json.loads(content) if ok else content
    except json.JSONDecodeError:
        parsed = content
    return {"seat": seat, "tool": tool, "ok": ok,
            "result" if ok else "refusal": parsed}


# --------------------------------------------------------------------------- #
# attention: who has to act, and what the desk handled on its own
# --------------------------------------------------------------------------- #

@app.get("/attention")
def attention(request: Request):
    """The CFO's view: what awaits the controller, what went to the lawyer, what the
    desk settled without asking anyone, and what was escalated."""
    cfg = _cfg(request)
    conn = _conn(cfg)
    try:
        def compact(item_id):
            b = brief_mod.brief(conn, item_id, cfg) or {}
            w = b.get("work_item") or {}
            prop = b.get("proposal") or {}
            debits = sum((float(p["amount"]) for p in prop.get("postings") or []
                          if p.get("side") == "debit"), 0.0)
            return {"id": w.get("id"), "kind": w.get("kind"), "state": w.get("state"),
                    "seat": w.get("seat"), "headline": b.get("headline"),
                    "plain": plain_mod.plain(conn, cfg, b),
                    "decision_needed": b.get("decision_needed"),
                    "amount": f"{debits:.2f}" if debits else None,
                    "key_points": (b.get("key_points") or [])[:3],
                    "policy": (prop.get("policy") or {}).get("ref"),
                    "questions": len(b.get("questions") or []),
                    "challenges": len(b.get("challenges") or []),
                    "approved_by": w.get("approved_by"),
                    "under_authorization": w.get("approved_by") == "standing_authorization",
                    "why_you": (b.get("authorization") or {}).get("reason")
                    if not (b.get("authorization") or {}).get("covered") else None,
                    "updated_at": w.get("updated_at")}

        proposed = [r[0] for r in conn.execute(
            "SELECT id FROM work_items WHERE state = 'PROPOSED' ORDER BY id")]
        for_controller = [compact(i) for i in proposed]

        routed = []
        for q in conn.execute(
                "SELECT q.*, w.kind, w.state AS item_state FROM questions q JOIN work_items w"
                " ON w.id = q.work_item_id ORDER BY q.created_at"):
            routed.append({"question_id": q["id"], "work_item": q["work_item_id"], "kind": q["kind"],
                           "to_role": q["to_role"], "text": q["text"], "state": q["state"],
                           "answer": q["answer"], "answered_by": q["answered_by"],
                           "item_state": q["item_state"],
                           "headline": brief_mod.KIND_TITLES.get(q["kind"], q["kind"])})

        asked = {r[0] for r in conn.execute("SELECT DISTINCT work_item_id FROM questions")}
        handled = []
        for r in conn.execute(
                "SELECT id FROM work_items WHERE state IN ('POSTED','CLOSED') AND seat IS NOT NULL"
                " AND kind NOT LIKE 'WI-ONBOARD-%' ORDER BY id"):
            if r[0] not in asked:
                handled.append(compact(r[0]))

        escalations = [dict(r) for r in conn.execute(
            "SELECT challenge_id, work_item_id, raised_by, text, classification, routed_to,"
            " changed, outcome, created_at FROM challenges ORDER BY created_at")]
        rules = [{"ref": p["ref"], "name": p["name"], "status": p["status"], "scope": p["scope"],
                  "proposed_by": p["proposed_by"], "approved_by": p["approved_by"],
                  "reason": p["reason"]} for p in policies.listing(conn)]
        violations = invariants.evaluate(conn, cfg)
    finally:
        conn.close()
    return {"for_controller": for_controller, "routed_to_lawyer": [r for r in routed if r["to_role"] == "human_lawyer"],
            "other_questions": [r for r in routed if r["to_role"] != "human_lawyer"],
            "handled_on_its_own": handled, "escalations": escalations, "rules": rules,
            "invariants": {"open": [v.as_dict() for v in violations], "titles": invariants.TITLES}}


# --------------------------------------------------------------------------- #
# issues: the work grouped by the event that caused it, in plain words
# --------------------------------------------------------------------------- #

def _awaiting(conn, item) -> str | None:
    if item["state"] == "PROPOSED":
        return "CFO"
    if item["state"] == "NEEDS_INFO":
        row = conn.execute(
            "SELECT to_role FROM questions WHERE work_item_id = ? AND state = 'OPEN' ORDER BY created_at DESC LIMIT 1",
            (item["id"],)).fetchone()
        if row:
            return row["to_role"].replace("human_", "")
        return "desk"                     # answered; the seat has to resume
    return None


@app.get("/issues")
def issues(request: Request):
    """Every event the desk is handling, with its stages, who each stage is waiting on,
    and plain-language titles. The main screens draw their trackers from this."""
    cfg = _cfg(request)
    conn = _conn(cfg)
    try:
        from .workflow import row_to_dict

        items = [row_to_dict(r) for r in conn.execute("SELECT * FROM work_items ORDER BY id")]
        by_id = {i["id"]: i for i in items}
        children: dict[str, list] = {}
        for i in items:
            if i.get("parent_id"):
                children.setdefault(i["parent_id"], []).append(i)

        def stage(i):
            b = brief_mod.brief(conn, i["id"], cfg) or {}
            p = plain_mod.plain(conn, cfg, b)
            return {"id": i["id"], "kind": i["kind"], "state": i["state"],
                    "title": p["title"], "summary": p["summary"], "amount": p["amount"],
                    "owner": p["proposer"], "awaiting": _awaiting(conn, i),
                    "updated_at": i["updated_at"]}

        out = []
        onboarding = [i for i in items if i["kind"].startswith("WI-ONBOARD")]
        if onboarding:
            stages = [stage(i) for i in onboarding]
            out.append({"id": "learning", "title": "Learning the rules from the policy and the books",
                        "kind": "onboarding", "state": "done" if all(s["state"] in ("CLOSED", "POSTED", "REJECTED") for s in stages) else "working",
                        "opened_at": onboarding[0]["created_at"], "stages": stages,
                        "awaiting": sorted({s["awaiting"] for s in stages if s["awaiting"]})})
        for i in items:
            if i["kind"].startswith("WI-ONBOARD") or i.get("parent_id"):
                continue
            kids = children.get(i["id"], [])
            head = stage(i)
            stages = [stage(k) for k in kids] if kids else [head]
            waiting = sorted({s["awaiting"] for s in stages if s["awaiting"]})
            people = [w for w in waiting if w != "desk"]
            settled = all(s["state"] in ("POSTED", "CLOSED", "REJECTED") for s in stages)
            out.append({"id": i["id"], "title": head["title"], "kind": i["kind"],
                        "state": "done" if settled else ("needs_you" if people else "working"),
                        "opened_at": i["created_at"], "stages": stages, "awaiting": waiting})
    finally:
        conn.close()
    return {"issues": out}


class SimulatedEvent(BaseModel):
    llm: str | None = Field(None, description="scripted | live; default live when a model is configured")
    controller: str = Field("human", description="human (you decide each entry) | scripted")


@app.post("/simulate/refund-arrives", status_code=202)
def simulate_refund(request: Request, body: SimulatedEvent = Body(default=SimulatedEvent())):
    """The demo's event: a CBP refund lands in the bank. Builds a clean sandbox world and
    starts the desk on it in the background; the agents work, the people react."""
    if MANAGER.active():
        raise RunBusy(f"{MANAGER.active().run_id} is still running")
    if body.llm not in (None, "live", "scripted"):
        raise HTTPException(400, "llm must be live or scripted")
    mode, note = body.llm, None
    if mode is None or mode == "live":
        usable, why = _llm_usable(_cfg(request))
        if not usable and mode == "live":
            raise HTTPException(503, "Live model unavailable. Check model access or choose Recorded replay.")
        mode = "live" if usable else "scripted"
        if not usable:
            note = "Recorded replay: no live model is available."
    name = "tariff-refund-demo"
    spec = scenario_mod.load(_scenario_path(name))
    cfg = scenario_mod.materialize(spec, CFG)
    db.rebuild_erp(cfg)
    conn = db.desk_conn(cfg)
    try:
        counts = sync.run(conn, cfg)
        before = scenario_mod.tie_outs(conn, cfg, spec, "before")
    finally:
        conn.close()
    report = {"scenario": name, "sandbox": str(cfg.root), "sync": counts,
              "before": {"checks": [c.as_dict() for c in before], **scenario_mod.summarize(before)},
              "after": None, "run": None}
    request.app.state.scenario = {"name": name, "spec": spec, "cfg": cfg, "report": report}
    request.app.state.cfg = cfg
    forwarded = ScenarioRun(llm=mode, controller=body.controller)
    return {"event": "refund-arrives", "world": name, "llm": mode, "controller": body.controller,
            "note": note, "run": run_scenario(name, request, forwarded)}


# --------------------------------------------------------------------------- #
# scenarios: a sandboxed world the whole API can switch onto
# --------------------------------------------------------------------------- #

def _scenario_files() -> list[dict[str, Any]]:
    out = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        try:
            spec = scenario_mod.load(path)
        except Exception as error:  # noqa: BLE001 - a broken file is listed, not fatal
            out.append({"name": path.stem, "file": path.name, "error": str(error)})
            continue
        out.append({"name": spec["name"], "file": path.name,
                    "description": (spec.get("description") or "").strip(),
                    "steps": list((spec.get("run") or {}).get("steps") or []),
                    "llm": (spec.get("run") or {}).get("llm"),
                    "known_issues": spec.get("known_issues") or []})
    return out


def _scenario_path(name: str) -> Path:
    for path in SCENARIOS_DIR.glob("*.yaml"):
        if path.stem == name:
            return path
    raise HTTPException(404, f"no scenario {name}; have {', '.join(p.stem for p in SCENARIOS_DIR.glob('*.yaml'))}")


@app.get("/scenarios")
def scenarios(request: Request):
    active = request.app.state.scenario
    return {"scenarios": _scenario_files(), "active": active["name"] if active else None}


@app.get("/scenarios/active")
def active_scenario(request: Request):
    active = request.app.state.scenario
    if not active:
        return {"active": None}
    run = MANAGER.active() or (MANAGER.get(active["run_id"]) if active.get("run_id") else None)
    return {"active": active["name"], "sandbox": str(active["cfg"].root),
            "steps": list((active["spec"].get("run") or {}).get("steps") or []),
            "llm": run.llm if run else (active["spec"].get("run") or {}).get("llm"),
            "controller": active.get("controller"),
            "report": active.get("report"), "run": (run.as_dict() | {"lines": None}) if run else None,
            "known_issues": active["spec"].get("known_issues") or []}


@app.post("/scenarios/{name}/activate")
def activate_scenario(name: str, request: Request):
    """Build the scenario's sandbox and point the whole API at it. Repo data is untouched."""
    if MANAGER.active():
        raise RunBusy(f"{MANAGER.active().run_id} is still running")
    spec = scenario_mod.load(_scenario_path(name))
    cfg = scenario_mod.materialize(spec, CFG)
    db.rebuild_erp(cfg)
    conn = db.desk_conn(cfg)
    try:
        counts = sync.run(conn, cfg)
        before = scenario_mod.tie_outs(conn, cfg, spec, "before")
    finally:
        conn.close()
    report = {"scenario": name, "sandbox": str(cfg.root), "sync": counts,
              "before": {"checks": [c.as_dict() for c in before], **scenario_mod.summarize(before)},
              "after": None, "run": None}
    request.app.state.scenario = {"name": name, "spec": spec, "cfg": cfg, "report": report}
    request.app.state.cfg = cfg
    return {"active": name, "sandbox": str(cfg.root), "report": report,
            "steps": list((spec.get("run") or {}).get("steps") or [])}


class ScenarioRun(BaseModel):
    llm: str | None = Field(None, description="scripted | live; defaults to the scenario's own")
    controller: str | None = Field(
        None, description="scripted (the persona decides at once) | human (each entry waits for "
                          "a person to approve in the UI); defaults to the scenario's own")


@app.post("/scenarios/{name}/run", status_code=202)
def run_scenario(name: str, request: Request, body: ScenarioRun = Body(default=ScenarioRun())):
    """Work the active scenario's world with the desk, on a background thread."""
    active = request.app.state.scenario
    if not active or active["name"] != name:
        raise HTTPException(409, f"activate {name} first (POST /scenarios/{name}/activate)")
    spec, cfg = active["spec"], active["cfg"]
    if not spec.get("run"):
        raise HTTPException(400, f"{name} has no run block; it is a tie-out-only scenario")
    mode = body.llm or spec["run"].get("llm") or "scripted"
    controller_mode = body.controller or spec["run"].get("controller") or "scripted"
    if mode == "live" and not _llm_ready(cfg):
        raise HTTPException(400, "live mode needs LLM_MODEL and an API key in .env")
    if controller_mode not in ("scripted", "human"):
        raise HTTPException(400, "controller must be scripted or human")

    def task(log) -> int:
        conn = db.desk_conn(cfg)
        try:
            try:
                summary = scenario_mod.run_desk(conn, cfg, spec, mode, log,
                                                controller_mode=controller_mode)
                after = (scenario_mod.tie_outs(conn, cfg, spec, "after")
                         + scenario_mod._run_expectations(conn, cfg, spec, summary))
            except Exception as error:  # noqa: BLE001 - the report must say why the run stopped
                summary = {"llm": mode, "error": f"{type(error).__name__}: {error}",
                           "posted_entries": conn.execute(
                               "SELECT COUNT(*) FROM work_items WHERE state='POSTED'").fetchone()[0]}
                after = [scenario_mod.Check("desk run", "run completed", "result",
                                            f"FAILED: {type(error).__name__}: {str(error)[:160]}",
                                            "expected", "completed", False)]
            report = active["report"]
            report["after"] = {"checks": [c.as_dict() for c in after], **scenario_mod.summarize(after)}
            report["run"] = summary
            report["passed"] = report["before"]["passed"] and report["after"]["passed"]
            db.log_event_now(conn, actor="sync", action="run:tie_outs", subject=None,
                             allowed=report["after"]["passed"],
                             reason=f"tie-outs after the run: {report['after']['ok']}/{report['after']['total']} ok",
                             evidence={"before": report["before"]["ok"], "after": report["after"]["ok"],
                                       "total": report["after"]["total"]})
            return 0 if report["passed"] else 1
        finally:
            conn.close()

    run = MANAGER.start_task(f"scenario:{name}", task, llm=mode)
    active["run_id"] = run.run_id
    active["controller"] = controller_mode
    active["report"]["after"] = None
    active["report"]["run"] = None
    return run.as_dict()


@app.post("/scenarios/deactivate")
def deactivate_scenario(request: Request):
    if MANAGER.active():
        raise RunBusy(f"{MANAGER.active().run_id} is still running")
    was = request.app.state.scenario
    request.app.state.scenario = None
    request.app.state.cfg = CFG
    return {"active": None, "was": was["name"] if was else None}


# --------------------------------------------------------------------------- #
# runs and reset
# --------------------------------------------------------------------------- #

class StartRun(BaseModel):
    phase: str = Field("phase3", description="phase1 | phase2 | phase3")
    llm: str = Field("scripted", description="scripted needs no model; live uses .env")


@app.post("/runs", status_code=202)
def start_run(request: Request, body: StartRun):
    if body.llm == "live" and not _llm_ready(_cfg(request)):
        raise HTTPException(400, "live mode needs LLM_MODEL and an API key in .env")
    run = MANAGER.start(body.phase, body.llm)
    return run.as_dict()


@app.get("/runs")
def runs():
    return {"runs": MANAGER.listing(), "active": (MANAGER.active().run_id if MANAGER.active() else None)}


@app.get("/runs/{run_id}")
def run_detail(run_id: str):
    run = MANAGER.get(run_id)
    if run is None:
        raise HTTPException(404, f"no run {run_id}")
    return run.as_dict()


@app.post("/reset", status_code=202)
def reset():
    """Rebuild the ERP, restore the posted book, drop the desk DB (cache kept)."""
    return MANAGER.start("reset", "scripted", action="reset").as_dict()


# --------------------------------------------------------------------------- #
# the built UI, when frontend/dist exists (python -m desk serve then opens it at /)
# --------------------------------------------------------------------------- #

_UI_DIST = CFG.root / "frontend" / "dist"
if _UI_DIST.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=str(_UI_DIST / "assets")), name="ui-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def ui(full_path: str):
        """The single-page app: a real file if there is one, else index.html so client
        routes like /live and /items/WI-0007 open directly. API routes are matched first."""
        candidate = _UI_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_UI_DIST / "index.html")
