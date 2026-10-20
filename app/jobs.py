"""Background report jobs. A report over a long period can take minutes, so the browser starts a
job, polls its progress and opens the result when it is done. Jobs live in memory for a while."""
import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

JOB_TTL = 2 * 3600  # finished reports stay available for downloads this long


@dataclass
class Job:
    id: str
    owner: str  # MultiProbe userID — only the author may see the report
    params: dict[str, Any]
    state: str = "running"  # running | done | error
    done_steps: int = 0
    total_steps: int = 1
    rows_fetched: int = 0
    error: str = ""
    result: Any = None
    created: float = field(default_factory=time.monotonic)

    def progress(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "percent": round(100 * self.done_steps / max(self.total_steps, 1)),
            "rows": self.rows_fetched,
            "error": self.error,
        }


_jobs: dict[str, Job] = {}
_tasks: set[asyncio.Task] = set()


def start(owner: str, params: dict[str, Any], work: Callable[[Job], Awaitable[Any]]) -> Job:
    _purge()
    job = Job(id=secrets.token_urlsafe(12), owner=owner, params=params)
    _jobs[job.id] = job

    async def run() -> None:
        try:
            job.result = await work(job)
            job.state = "done"
        except Exception as exc:  # reported to the user on the progress page
            job.error = str(exc) or exc.__class__.__name__
            job.state = "error"

    task = asyncio.create_task(run())
    _tasks.add(task)  # keep a reference so the task is not garbage-collected
    task.add_done_callback(_tasks.discard)
    return job


def get(job_id: str, owner: str) -> Job | None:
    job = _jobs.get(job_id)
    return job if job and job.owner == owner else None


def _purge() -> None:
    now = time.monotonic()
    for job_id in [j.id for j in _jobs.values() if j.state != "running" and now - j.created > JOB_TTL]:
        _jobs.pop(job_id, None)
