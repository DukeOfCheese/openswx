"""Background jobs: long commands (simulation, flow, CAM, plastics) run on a worker thread, report progress and
can be cancelled, while the application keeps answering.

A solver calls `jobs.progress(fraction, message)` now and then; when the job was cancelled that call raises
`Cancelled`, which ends the command. Called outside a job it does nothing, so solvers need not care how they
were started.
"""

from __future__ import annotations

import itertools
import threading
import time

_TL = threading.local()
_jobs: dict[str, dict] = {}
_ids = itertools.count(1)
_lock = threading.Lock()
KEEP = 50  # finished jobs remembered


class Cancelled(RuntimeError):
    pass


def progress(fraction: float | None = None, message: str | None = None) -> None:
    """Report how far the running job is (0..1) and what it is doing; raises Cancelled if it was cancelled."""
    job = getattr(_TL, "job", None)
    if job is None:
        return
    if job["cancel"].is_set():
        raise Cancelled("cancelled")
    if fraction is not None:
        job["progress"] = max(0.0, min(1.0, float(fraction)))
    if message is not None:
        job["message"] = message


def start(name: str, fn, label: str | None = None) -> str:
    """Run fn() on a worker thread as job `name`; returns the job id."""
    with _lock:
        jid = f"job{next(_ids)}"
        job = {"id": jid, "name": name, "label": label or name, "state": "running", "started": time.time(),
               "finished": None, "progress": None, "message": None, "result": None, "error": None,
               "cancel": threading.Event()}
        _jobs[jid] = job
        done = [k for k, j in _jobs.items() if j["state"] != "running"]
        for k in done[:-KEEP]:
            del _jobs[k]

    def work():
        _TL.job = job
        try:
            job["result"] = fn()
            job["state"] = "cancelled" if job["cancel"].is_set() else "done"
            if job["state"] == "cancelled":
                job["result"] = None  # (finished after the cancel: its result is not wanted)
        except Cancelled:
            job["state"] = "cancelled"
        except Exception as exc:  # noqa: BLE001 - reported through the job
            cause = exc.__cause__
            job["state"] = "cancelled" if isinstance(cause, Cancelled) or job["cancel"].is_set() else "error"
            job["error"] = None if job["state"] == "cancelled" else str(exc)
        finally:
            job["finished"] = time.time()
            job["progress"] = 1.0 if job["state"] == "done" else job["progress"]
            _TL.job = None

    t = threading.Thread(target=work, name=f"openswx-{jid}", daemon=True)
    job["thread"] = t
    t.start()
    return jid


def public(job: dict, with_result: bool = True) -> dict:
    out = {k: job[k] for k in ("id", "name", "label", "state", "progress", "message", "error")}
    out["seconds"] = round((job["finished"] or time.time()) - job["started"], 2)
    if with_result and job["state"] == "done":
        out["result"] = job["result"]
    return out


def get(jid: str) -> dict | None:
    return _jobs.get(jid)


def all_jobs() -> list[dict]:
    return list(_jobs.values())


def cancel(jid: str) -> bool:
    job = _jobs.get(jid)
    if job is None or job["state"] != "running":
        return False
    job["cancel"].set()
    return True


def wait(jid: str, timeout: float | None = None) -> dict:
    job = _jobs[jid]
    job["thread"].join(timeout)
    return job
