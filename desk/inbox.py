"""The inbox: how a human puts a document, or a link, in front of the desk.

Only an inbox document can change a proposal (CLAUDE.md 6.8). A counter-argument
typed into a chat box is opinion; this module is how it becomes evidence: it is
written to data/documents/inbox/ with frontmatter the verifier can check
(doc_id, parties, dates, relates_to), and from then on it is a citable document
like anything else that arrived.

A link is fetched once and stored as text with its source_url, so what the desk
read is on file and checkable later even if the page changes.
"""
from __future__ import annotations

import html
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import CFG, Config
from .db import log_event_now
from .evidence import INBOX, frontmatter

MAX_FETCH_BYTES = 400_000
DOC_PREFIX = "DOC-UPL-"


class InboxError(Exception):
    pass


def inbox_dir(cfg: Config = CFG) -> Path:
    return cfg.root / INBOX


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:48] or "document"


def next_doc_id(cfg: Config = CFG) -> str:
    highest = 0
    folder = inbox_dir(cfg)
    if folder.is_dir():
        for path in folder.glob("*.md"):
            meta = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            doc_id = str(meta.get("doc_id") or "")
            if doc_id.startswith(DOC_PREFIX) and doc_id[len(DOC_PREFIX):].isdigit():
                highest = max(highest, int(doc_id[len(DOC_PREFIX):]))
    return f"{DOC_PREFIX}{highest + 1:04d}"


def fetch_url(url: str, *, timeout: int = 15) -> str:
    """Fetch a public http(s) page and return its visible text."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise InboxError(f"refused: only http(s) links can be fetched, got {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": "BrewlineTariffDesk/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_FETCH_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise InboxError(f"could not fetch {url}: {error}") from error
    if len(raw) > MAX_FETCH_BYTES:
        raise InboxError(f"refused: {url} is larger than {MAX_FETCH_BYTES} bytes")
    text = raw.decode(charset, errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def add_document(
    cfg: Config = CFG,
    *,
    title: str,
    body: str | None = None,
    url: str | None = None,
    doc_type: str = "uploaded_evidence",
    sender: str | None = None,
    parties: list[str] | None = None,
    doc_date: str | None = None,
    relates_to: list[str] | None = None,
    uploaded_by: str = "human_controller",
    work_item_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Write one inbox document and return its path and doc_id.

    `body` is the document text. If only `url` is given the page is fetched and
    its text stored, with the link kept as source_url. Either way the file gets
    a doc_id, so the verifier can cite it, and `relates_to` names the contracts
    it bears on, which the verifier checks against the work item.
    """
    if not title or not title.strip():
        raise InboxError("a title is required")
    if not body and not url:
        raise InboxError("give the document text (body) or a link (url) to fetch")
    text = body.strip() if body else fetch_url(url)
    if not text:
        raise InboxError("the document is empty")
    if doc_date:
        try:
            date.fromisoformat(doc_date)
        except ValueError as error:
            raise InboxError(f"date {doc_date!r} must be YYYY-MM-DD") from error

    today = datetime.now(timezone.utc).date().isoformat()
    doc_id = next_doc_id(cfg)
    folder = inbox_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{today}-{_slug(title)}"
    path = folder / f"{stem}.md"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}.md"
        n += 1

    lines = ["---", f"doc_id: {doc_id}", f"type: {doc_type}", f"date: {doc_date or today}",
             f"uploaded: {today}", f"uploaded_by: {uploaded_by}"]
    if sender:
        lines.append(f"from: {sender}")
    if parties:
        lines.append(f"parties: [{', '.join(p.strip() for p in parties if p.strip())}]")
    if relates_to:
        lines.append(f"relates_to: [{', '.join(r.strip() for r in relates_to if r.strip())}]")
    if url:
        lines.append(f"source_url: {url}")
    if work_item_id:
        lines.append(f"work_item: {work_item_id}")
    lines += ["---", "", f"# {title.strip()}", "", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")

    relative = path.relative_to(cfg.root).as_posix()
    if conn is not None:
        log_event_now(
            conn, actor=uploaded_by, action="inbox:upload", subject=work_item_id, allowed=True,
            reason=f"{doc_id} {relative}: {title.strip()[:120]}",
            evidence={"doc_id": doc_id, "path": relative, "type": doc_type,
                      "source_url": url, "relates_to": relates_to or [], "bytes": len(text)},
        )
    return {"path": relative, "doc_id": doc_id, "bytes": len(text), "title": title.strip(),
            "source_url": url, "relates_to": relates_to or []}


def list_documents(cfg: Config = CFG) -> list[dict[str, Any]]:
    """Every document the desk may read, with its frontmatter."""
    out = []
    for folder in cfg.doc_dirs:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            meta: dict[str, Any] = {}
            if path.suffix.lower() in (".md", ".txt"):
                meta = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            out.append({
                "path": path.relative_to(cfg.root).as_posix(),
                "folder": folder.relative_to(cfg.root).as_posix(),
                "doc_id": meta.get("doc_id"),
                "type": meta.get("type"),
                "date": meta.get("date"),
                "relates_to": meta.get("relates_to"),
                "pushback_class_expected": meta.get("pushback_class_expected"),
                "bytes": path.stat().st_size,
            })
    return out
