"""Small in-process job registry with monotonic progress and cancellation."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .errors import ErrorCode, ResearchError


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    job_id: str
    kind: str
    total: int
    completed: int = 0
    state: JobState = JobState.PENDING
    message: str = ""
    cursor: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def progress(self) -> float:
        return 0.0 if self.total == 0 else min(1.0, self.completed / self.total)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "total": self.total,
            "completed": self.completed,
            "progress": self.progress,
            "state": self.state.value,
            "message": self.message,
            "cursor": self.cursor,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class JobService:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def create(self, kind: str, *, total: int = 0, cursor: str | None = None) -> Job:
        if not kind or total < 0:
            raise ResearchError(ErrorCode.INVALID_ARGUMENT, "invalid job kind or total")
        job = Job(job_id=str(uuid.uuid4()), kind=kind, total=total, cursor=cursor)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as error:
                raise ResearchError(ErrorCode.NOT_FOUND, "job not found") from error

    def update(
        self,
        job_id: str,
        *,
        completed: int,
        message: str = "",
        cursor: str | None = None,
    ) -> Job:
        with self._lock:
            job = self.get(job_id)
            if job.state in {JobState.CANCELLED, JobState.SUCCEEDED, JobState.FAILED}:
                raise ResearchError(ErrorCode.CONFLICT, "job is already terminal")
            if completed < job.completed or (job.total and completed > job.total):
                raise ResearchError(ErrorCode.CONFLICT, "job progress must be monotonic")
            job.completed = completed
            job.message = message
            job.cursor = cursor
            job.state = JobState.RUNNING
            job.updated_at = datetime.now(timezone.utc)
            return job

    def complete(self, job_id: str, *, message: str = "") -> Job:
        with self._lock:
            job = self.get(job_id)
            if job.state is JobState.CANCELLED:
                raise ResearchError(ErrorCode.CONFLICT, "cancelled job cannot complete")
            job.completed = job.total
            job.message = message
            job.state = JobState.SUCCEEDED
            job.updated_at = datetime.now(timezone.utc)
            return job

    def fail(self, job_id: str, *, message: str) -> Job:
        with self._lock:
            job = self.get(job_id)
            job.message = message
            job.state = JobState.FAILED
            job.updated_at = datetime.now(timezone.utc)
            return job

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self.get(job_id)
            if job.state in {JobState.SUCCEEDED, JobState.FAILED}:
                raise ResearchError(ErrorCode.CONFLICT, "terminal job cannot be cancelled")
            job.state = JobState.CANCELLED
            job.updated_at = datetime.now(timezone.utc)
            return job

