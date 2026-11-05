from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, TypeVar


T = TypeVar("T")


def submission_progress(total: int, attempts: list[dict[str, Any]]) -> dict[str, int]:
    """Build the stable progress contract returned to the browser."""
    statuses = {
        "queued": 0,
        "submitting": 0,
        "submitted": 0,
        "failed": 0,
        "uncertain": 0,
    }
    for attempt in attempts:
        status = str(attempt.get("status") or "")
        if status in statuses:
            statuses[status] += 1
    completed = statuses["submitted"]
    return {
        "total": total,
        "completed": completed,
        "queued": statuses["queued"],
        "processing": statuses["submitting"],
        "failed": statuses["failed"],
        "uncertain": statuses["uncertain"],
        "percent": int(round((completed / total) * 100)) if total else 0,
    }


async def run_limited(
    items: Iterable[T], worker: Callable[[T], Awaitable[None]], limit: int
) -> None:
    """Run independent items concurrently while keeping a strict upper bound."""
    semaphore = asyncio.Semaphore(max(1, min(5, int(limit))))

    async def run_one(item: T) -> None:
        async with semaphore:
            await worker(item)

    await asyncio.gather(*(run_one(item) for item in items))
