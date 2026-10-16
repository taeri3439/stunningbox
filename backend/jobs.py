"""In-memory progress registry for long-running document jobs.

A PDF's analyze+translate pipeline runs ~1-2 min, so the upload endpoint kicks it
off on a background thread and returns immediately; the frontend polls `status` to
render a live progress bar. State lives in this process only — the *result* is
persisted to disk by the pipeline, so `result` works even if a restart drops the
job entry (status then falls back to "done if the translation file exists").
"""
from __future__ import annotations

import threading

# doc_id -> {status, stage, done, total, error, source_lang, target_lang}
#   status: "running" | "done" | "error"
#   stage:  "extract" | "analyze" | "translate" | "done"
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def start(doc_id: str, source_lang: str, target_lang: str) -> None:
    with _LOCK:
        _JOBS[doc_id] = {
            "status": "running",
            "stage": "extract",
            "done": 0,
            "total": 0,
            "error": None,
            "source_lang": source_lang,
            "target_lang": target_lang,
        }


def progress(doc_id: str, stage: str, done: int, total: int) -> None:
    with _LOCK:
        job = _JOBS.get(doc_id)
        if job is not None:
            job.update(stage=stage, done=done, total=total)


def finish(doc_id: str) -> None:
    with _LOCK:
        job = _JOBS.get(doc_id)
        if job is not None:
            job.update(status="done", stage="done")


def fail(doc_id: str, message: str) -> None:
    with _LOCK:
        job = _JOBS.get(doc_id)
        if job is not None:
            job.update(status="error", error=message)


def get(doc_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(doc_id)
        return dict(job) if job is not None else None
