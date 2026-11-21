"""Configuration. Everything comes from .env in the repo root; nothing is hardcoded."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


def repo_root() -> Path:
    """The directory holding CLAUDE.md, data/ and systems/."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "systems").is_dir() and (parent / "data").is_dir():
            return parent
    raise RuntimeError(f"cannot locate repo root from {here}")


ROOT = repo_root()
load_dotenv(ROOT / ".env")


def _path(key: str, default: str) -> Path:
    raw = os.getenv(key) or default
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


# The Creao agents' app-scoped keys (brewline_agents_integration.md). Each may be named
# the canonical way or the way it was pasted into .env (spaces and case are ignored).
CREAO_KEY_NAMES = {
    "tariffs": ("CREAO_TARIFFS_REFUNDS_KEY", "Tariffs_Refunds_KEY", "TARIFFS_REFUNDS_KEY"),
    "sourcing": ("CREAO_SOURCING_KEY", "Sourcing_analyst_KEY", "SOURCING_ANALYST_KEY"),
    "stock": ("CREAO_STOCK_CUSTOMERS_KEY", "STOCK_CUSTOMER_KEY", "STOCK_CUSTOMERS_KEY"),
}
CREAO_APP_NAMES = {
    "tariffs": "CREAO_APP_TARIFFS_REFUNDS",
    "sourcing": "CREAO_APP_SOURCING",
    "stock": "CREAO_APP_STOCK_CUSTOMERS",
}


def _normal(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def loose_env(path: Path) -> dict[str, str]:
    """Every KEY=value in a .env file, keys normalised (case, spaces and underscores
    dropped), so `Tariffs_Refunds _KEY =...` is found as well as CREAO_TARIFFS_REFUNDS_KEY."""
    found: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        m = re.match(r"^\s*(?:export\s+)?([^=#]+?)\s*=\s*(.*?)\s*$", line)
        if not m:
            continue
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[_normal(m.group(1))] = value
    return found


def creao_keys(path: Path = ROOT / ".env", environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """agent -> capi_ key, from the environment first, then the .env file loosely."""
    environ = os.environ if environ is None else environ
    loose = loose_env(path)
    keys: dict[str, str] = {}
    for agent, names in CREAO_KEY_NAMES.items():
        value = ""
        for name in names:
            value = (environ.get(name) or "").strip()
            if value:
                break
        if not value:
            for name in names:
                value = loose.get(_normal(name), "").strip()
                if value:
                    break
        if value:
            keys[agent] = value
    return keys


@dataclass(frozen=True)
class Config:
    root: Path
    provider: str
    model: str
    model_fast: str
    openai_api_key: str
    anthropic_api_key: str
    beancount_file: Path
    erp_db: Path
    desk_db: Path
    cache_db: Path
    creao_base_url: str = "https://agent.creao.ai"
    creao_keys: dict[str, str] = field(default_factory=dict)
    creao_apps: dict[str, str] = field(default_factory=dict)
    creao_dry_run: bool = False
    creao_timeout: int = 300

    # Directories the desk may read documents from. data/truth and
    # data/answer_key are deliberately absent (CLAUDE.md section 7 rule 1).
    @property
    def doc_dirs(self) -> tuple[Path, ...]:
        return (self.root / "data" / "documents", self.root / "data" / "reference")

    @property
    def posted_file(self) -> Path:
        return self.root / "systems" / "books" / "posted" / "desk.beancount"

    @property
    def answer_key(self) -> Path:
        """Read by desk/score.py only. No agent tool may reach it."""
        return self.root / "data" / "answer_key" / "answer_key.json"

    @property
    def erp_schema(self) -> Path:
        return self.root / "systems" / "erp" / "schema.sql"

    @property
    def erp_seed(self) -> Path:
        return self.root / "systems" / "erp" / "seed.sql"

    @property
    def api_key(self) -> str:
        if self.provider == "creao":
            return next(iter(self.creao_keys.values()), "")
        return self.openai_api_key if self.provider == "openai" else self.anthropic_api_key

    @property
    def live_ready(self) -> bool:
        """Can the seats run on a live brain? Creao needs the two agents the refund flow
        uses (tariffs, stock); a model provider needs its key and a model id."""
        if self.provider == "creao":
            return bool(self.creao_keys.get("tariffs") and self.creao_keys.get("stock"))
        return bool(self.api_key and self.model)

    @property
    def fast_provider(self) -> str | None:
        """The model provider for the small, cheap calls (pushback classification). The
        Creao agents are not a model; those calls use whichever model key is present."""
        if self.provider in ("openai", "anthropic") and self.api_key and self.model_fast:
            return self.provider
        if self.anthropic_api_key and self.model_fast:
            return "anthropic"
        if self.openai_api_key and self.model_fast:
            return "openai"
        return None


def load() -> Config:
    return Config(
        root=ROOT,
        provider=(os.getenv("LLM_PROVIDER") or "openai").strip().lower(),
        model=(os.getenv("LLM_MODEL") or "").strip(),
        model_fast=(os.getenv("LLM_MODEL_FAST") or "").strip(),
        openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
        anthropic_api_key=(os.getenv("ANTHROPIC_API_KEY") or "").strip(),
        beancount_file=_path("BEANCOUNT_FILE", "systems/books/main.beancount"),
        erp_db=_path("ERP_DB", "systems/erp/erp.db"),
        desk_db=_path("DESK_DB", "systems/desk/desk.db"),
        cache_db=_path("LLM_CACHE_DB", ".cache/llm_cache.db"),
        creao_base_url=(os.getenv("CREAO_BASE_URL") or "https://agent.creao.ai").strip().rstrip("/"),
        creao_keys=creao_keys(),
        creao_apps={agent: (os.getenv(name) or "").strip() for agent, name in CREAO_APP_NAMES.items()
                    if (os.getenv(name) or "").strip()},
        creao_dry_run=(os.getenv("CREAO_DRY_RUN") or "").strip().lower() in ("1", "true", "yes"),
        creao_timeout=int((os.getenv("CREAO_RUN_TIMEOUT") or "300").strip() or 300),
    )


CFG = load()
