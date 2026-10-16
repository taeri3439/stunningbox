"""Bounded-concurrency ordered map for the Claude-bound pipeline stages.

Per-page structure analysis and per-chunk translation are independent, network-bound
Claude calls. Running them one-at-a-time makes a long document's latency the *sum* of
every call, which blows past the request timeout. Mapping them concurrently makes it
the *slowest single call* instead. Bounded so we don't trip API rate limits.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# Concurrent in-flight Claude calls per stage. Override via env if rate limits bite.
PIPELINE_CONCURRENCY = int(os.environ.get("PIPELINE_CONCURRENCY", "6"))


def parallel_map(
    fn: Callable[[T], R],
    items: Sequence[T],
    workers: int | None = None,
    on_done: Optional[Callable[[], None]] = None,
) -> list[R]:
    """Apply `fn` to each item concurrently, preserving input order.

    `on_done` (if given) is invoked once per item as it finishes — used to drive a
    live progress counter. It may run on any worker thread, so it must be cheap and
    thread-safe. Exceptions raised in a worker propagate to the caller (when the
    result list is materialized), so callers' existing try/except still surfaces them.
    """
    items = list(items)
    n = workers or PIPELINE_CONCURRENCY
    if len(items) <= 1 or n <= 1:
        out = []
        for x in items:
            out.append(fn(x))
            if on_done:
                on_done()
        return out
    with ThreadPoolExecutor(max_workers=min(n, len(items))) as ex:
        futures = [ex.submit(fn, x) for x in items]
        if on_done:
            for _ in as_completed(futures):
                on_done()
        return [f.result() for f in futures]  # input order; re-raises worker errors
