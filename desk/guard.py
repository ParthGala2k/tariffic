"""The hard guard: the desk must never read data/truth/ or data/answer_key/.

CLAUDE.md section 7 rule 1. Refusals raise Refused and are logged by the caller.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import CFG, Config

# Path prefixes the desk may never read, however the request is spelled.
FORBIDDEN_DIRS = ("data/truth", "data/answer_key")

# run_sql may only read the golden tables produced by the sync layer.
GOLDEN_PREFIX = "golden_"

_WRITE_WORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "attach", "detach", "pragma", "vacuum", "reindex", "begin", "commit",
    "rollback", "truncate",
)


class Refused(Exception):
    """A read was refused by the guard."""

    def __init__(self, reason: str, *, target: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.target = target


def _norm(text: str) -> str:
    return text.replace("\\", "/").lower()


def _mentions_forbidden(text: str) -> str | None:
    low = _norm(text)
    for forbidden in FORBIDDEN_DIRS:
        if forbidden in low:
            return forbidden
        # also catch a bare directory name, e.g. "truth/world.json"
        leaf = forbidden.split("/")[-1]
        if re.search(rf"(^|[/\s'\"]){leaf}/", low):
            return forbidden
    return None


def check_doc_path(raw_path: str, cfg: Config = CFG) -> Path:
    """Resolve a document path and refuse anything outside the allowed dirs.

    Returns the resolved path. Refuses forbidden directories explicitly first,
    so the audit reason says *why* rather than just "outside allowed dirs".
    """
    hit = _mentions_forbidden(raw_path)
    if hit:
        raise Refused(
            f"refused: {hit}/ is ground truth and the desk may never read it (CLAUDE.md 7.1)",
            target=raw_path,
        )

    candidate = Path(raw_path)
    resolved = (candidate if candidate.is_absolute() else cfg.root / candidate).resolve()

    hit = _mentions_forbidden(str(resolved))
    if hit:
        raise Refused(
            f"refused: {hit}/ is ground truth and the desk may never read it (CLAUDE.md 7.1)",
            target=raw_path,
        )

    for allowed in cfg.doc_dirs:
        allowed_root = allowed.resolve()
        if resolved == allowed_root:
            return resolved
        try:
            resolved.relative_to(allowed_root)
        except ValueError:
            continue
        return resolved

    allowed_names = ", ".join(d.relative_to(cfg.root).as_posix() + "/" for d in cfg.doc_dirs)
    raise Refused(
        f"refused: read_doc may only read {allowed_names} (asked for {raw_path})",
        target=raw_path,
    )


def check_sql(sql: str) -> str:
    """Refuse anything that is not a single read-only SELECT over golden tables."""
    stripped = re.sub(r"--[^\n]*", " ", sql)
    stripped = re.sub(r"/\*.*?\*/", " ", stripped, flags=re.S).strip().rstrip(";")

    hit = _mentions_forbidden(stripped)
    if hit:
        raise Refused(
            f"refused: {hit}/ is ground truth and the desk may never read it (CLAUDE.md 7.1)",
            target=sql,
        )

    if ";" in stripped:
        raise Refused("refused: run_sql takes exactly one statement", target=sql)

    low = stripped.lower()
    if not re.match(r"^(select|with)\b", low):
        raise Refused("refused: run_sql is read-only; only SELECT (or WITH ... SELECT)", target=sql)

    for word in _WRITE_WORDS:
        if re.search(rf"\b{word}\b", low):
            raise Refused(f"refused: run_sql is read-only; {word.upper()} is not allowed", target=sql)

    tables = {t for t in re.findall(r"\b(?:from|join)\s+([A-Za-z_][\w.]*)", low)}
    ctes = {c for c in re.findall(r"\b([A-Za-z_]\w*)\s+as\s*\(", low)}
    tables -= ctes
    if not tables:
        raise Refused("refused: run_sql could not find a table to read", target=sql)

    offenders = sorted(t for t in tables if not t.startswith(GOLDEN_PREFIX))
    if offenders:
        raise Refused(
            f"refused: run_sql may only read golden_* tables; asked for {', '.join(offenders)}",
            target=sql,
        )
    return stripped
