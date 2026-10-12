#!/usr/bin/env python3
"""Run long webui operations off the event loop.

Calling blocking code straight from an `async def` endpoint stalls Uvicorn's
single event loop: while a long download runs, EVERY request queues behind it
and the whole UI looks dead, not just the tab that started it. On top of that
the browser sits on a synchronous POST long past its own timeout and reports a
failure for a job that goes on to finish cleanly.

A job here runs in a worker thread and reports state through `status()`, so
the endpoint can redirect immediately and the page can poll.

SQLite connections are per-thread (db.get_connection does not pass
check_same_thread), so a runner must open its OWN connection inside the
thread rather than capturing one from the request.
"""
import datetime
import threading
import traceback

_LOCK = threading.Lock()
_JOBS = {}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def status(name):
    """Current state of `name`, or an idle record if it never ran."""
    with _LOCK:
        job = _JOBS.get(name)
        return dict(job) if job else {
            "name": name, "running": False, "started_at": None,
            "finished_at": None, "message": None, "error": None,
        }


def all_status():
    with _LOCK:
        return {name: dict(job) for name, job in _JOBS.items()}


def set_progress(name, text):
    """Progress line of a running job (e.g. "complete 40/238"), shown by the
    page banner next to "running". No-op when `name` is not running."""
    with _LOCK:
        job = _JOBS.get(name)
        if job and job["running"]:
            job["progress"] = text


def is_running(name):
    return status(name)["running"]


def start(name, fn):
    """Run `fn` in a worker thread unless `name` is already in flight.

    `fn` takes no arguments and returns the human-facing completion message.
    Returns (started: bool, message: str) — a False means a job was already
    running, which the caller should surface rather than silently queueing a
    second run of the same job on top of the first.
    """
    with _LOCK:
        existing = _JOBS.get(name)
        if existing and existing["running"]:
            return False, (
                f"Ya hay una tarea '{name}' en curso (empezó {existing['started_at'][11:16]} UTC). "
                f"Esperá a que termine antes de lanzar otra."
            )
        _JOBS[name] = {
            "name": name, "running": True, "started_at": _now(),
            "finished_at": None, "message": None, "error": None,
            "progress": None,
        }

    def _run():
        try:
            result = fn()
            with _LOCK:
                _JOBS[name].update(message=result or "finished", running=False,
                                   finished_at=_now())
        except Exception as exc:
            # Never let a worker thread die silently — an unreported failure
            # here reads exactly like the "still running" state.
            with _LOCK:
                _JOBS[name].update(
                    running=False, finished_at=_now(),
                    error=f"{type(exc).__name__}: {exc}",
                    message=None,
                )
            traceback.print_exc()

    threading.Thread(target=_run, name=f"webui-job-{name}", daemon=True).start()
    return True, f"'{name}' en curso"


def reset(name=None):
    """Clear job state. Tests only."""
    with _LOCK:
        if name is None:
            _JOBS.clear()
        else:
            _JOBS.pop(name, None)
