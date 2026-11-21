"""Background runs for the API: start a phase, watch it, read its output.

The CLI pipelines in desk/__main__.py print their progress. The run manager
executes one of them on a worker thread, captures that thread's stdout line by
line, and keeps the run's state in memory. Progress itself is already in the
audit log, which is where the UI reads it; the captured lines are for the
operator's console view.

Only one run at a time: the desk DB and the posted book are single-writer.
"""
from __future__ import annotations

import argparse
import sys
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

PHASES = ("phase1", "phase2", "phase3")
LLM_MODES = ("scripted", "live")


class RunBusy(Exception):
    pass


class _ThreadStdout:
    """A stdout that routes each worker thread's prints into its own buffer."""

    def __init__(self, original):
        self.original = original
        self.buffers: dict[int, list[str]] = {}
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        buffer = self.buffers.get(threading.get_ident())
        if buffer is None:
            return self.original.write(text)
        with self._lock:
            buffer.append(text)
        return len(text)

    def flush(self) -> None:
        self.original.flush()

    def __getattr__(self, name):          # isatty, encoding, fileno, ...
        return getattr(self.original, name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Run:
    run_id: str
    phase: str
    llm: str
    state: str = "queued"                 # queued | running | finished | failed
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    _chunks: list[str] = field(default_factory=list, repr=False)

    @property
    def output(self) -> str:
        return "".join(self._chunks)

    def as_dict(self) -> dict[str, Any]:
        text = self.output
        return {
            "run_id": self.run_id, "phase": self.phase, "llm": self.llm, "state": self.state,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "exit_code": self.exit_code, "error": self.error,
            "lines": text.splitlines(), "passed": self.exit_code == 0,
        }


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self._active: Run | None = None
        self._tee: _ThreadStdout | None = None

    # ---- public --------------------------------------------------------------
    def start(self, phase: str, llm: str = "scripted", *, action: str = "run") -> Run:
        if action == "run" and phase not in PHASES:
            raise ValueError(f"phase must be one of {', '.join(PHASES)}")
        if llm not in LLM_MODES:
            raise ValueError(f"llm must be one of {', '.join(LLM_MODES)}")
        with self._lock:
            if self._active is not None and self._active.state in ("queued", "running"):
                raise RunBusy(f"{self._active.run_id} ({self._active.phase}) is still running")
            run = Run(run_id=f"RUN-{len(self._runs) + 1:04d}", phase=phase, llm=llm)
            self._runs[run.run_id] = run
            self._active = run
        thread = threading.Thread(target=self._worker, args=(run, action), daemon=True,
                                  name=run.run_id)
        thread.start()
        return run

    def start_task(self, name: str, fn, *, llm: str = "scripted") -> Run:
        """Run `fn(log)` on a worker thread as a run called `name` (e.g. scenario:rosy-baseline).

        `fn` receives a log callable and returns an exit code (None counts as 0).
        """
        with self._lock:
            if self._active is not None and self._active.state in ("queued", "running"):
                raise RunBusy(f"{self._active.run_id} ({self._active.phase}) is still running")
            run = Run(run_id=f"RUN-{len(self._runs) + 1:04d}", phase=name, llm=llm)
            self._runs[run.run_id] = run
            self._active = run
        thread = threading.Thread(target=self._worker_task, args=(run, fn), daemon=True,
                                  name=run.run_id)
        thread.start()
        return run

    def _worker_task(self, run: Run, fn) -> None:
        tee = self._install()
        tee.buffers[threading.get_ident()] = run._chunks
        run.state, run.started_at = "running", _now()
        try:
            code = fn(lambda *parts: print(*parts))
            run.exit_code = 0 if (code is None or code is True) else int(code)
            run.state = "finished"
        except Exception as error:  # noqa: BLE001 - the run must record its own failure
            run.error = f"{type(error).__name__}: {error}"
            run._chunks.append("\n" + traceback.format_exc()[-3000:])
            run.exit_code, run.state = 2, "failed"
        finally:
            run.finished_at = _now()
            tee.buffers.pop(threading.get_ident(), None)
            self._uninstall_if_idle()

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def listing(self) -> list[dict[str, Any]]:
        return [r.as_dict() | {"lines": None} for r in self._runs.values()]

    def active(self) -> Run | None:
        run = self._active
        return run if run is not None and run.state in ("queued", "running") else None

    def wait(self, run_id: str, timeout: float = 120.0) -> Run:
        """Block until the run settles (for tests and scripts)."""
        import time

        run = self._runs[run_id]
        deadline = time.monotonic() + timeout
        while run.state in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.05)
        return run

    # ---- internals -------------------------------------------------------------
    def _install(self) -> _ThreadStdout:
        if not isinstance(sys.stdout, _ThreadStdout):
            sys.stdout = _ThreadStdout(sys.stdout)
        return sys.stdout            # type: ignore[return-value]

    def _uninstall_if_idle(self) -> None:
        tee = sys.stdout
        if isinstance(tee, _ThreadStdout) and not tee.buffers:
            sys.stdout = tee.original

    def _worker(self, run: Run, action: str) -> None:
        from . import __main__ as cli
        from . import db

        tee = self._install()
        tee.buffers[threading.get_ident()] = run._chunks
        run.state, run.started_at = "running", _now()
        try:
            args = argparse.Namespace(llm=run.llm, phase=run.phase, clear_cache=False)
            if action == "reset":
                code = cli.cmd_reset(args)
            else:
                code = {"phase1": cli.cmd_run_phase1, "phase2": cli.cmd_run_phase2,
                        "phase3": cli.cmd_run_phase3}[run.phase](args)
            run.exit_code = int(code)
            run.state = "finished"
        except Exception as error:  # noqa: BLE001 - the run must record its own failure
            run.error = f"{type(error).__name__}: {error}"
            run._chunks.append("\n" + traceback.format_exc()[-3000:])
            run.exit_code, run.state = 2, "failed"
        finally:
            run.finished_at = _now()
            tee.buffers.pop(threading.get_ident(), None)
            self._uninstall_if_idle()
            try:
                conn = db.desk_conn(cli.CFG)
                try:
                    db.log_event_now(
                        conn, actor="sync", action=f"run:{action}", subject=None,
                        allowed=run.exit_code == 0,
                        reason=(f"{run.run_id} {run.phase} ({run.llm}) "
                                f"{run.state} with exit code {run.exit_code}"
                                + (f": {run.error}" if run.error else "")),
                        evidence={"run_id": run.run_id, "phase": run.phase, "llm": run.llm},
                    )
                finally:
                    conn.close()
            except Exception:  # noqa: BLE001 - a failed log must not hide the run's result
                pass


MANAGER = RunManager()
