import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .core.progress import Steps

STALL_AFTER = 60.0


@dataclass
class Step:
    id: str
    label: str
    optional: bool = False
    status: str = "pending"
    progress: float = 0.0
    detail: str = ""
    estimated: bool = False
    expected: float | None = None
    hint: str | None = None
    started: float | None = None
    finished: float | None = None
    last_change: float | None = None

    def to_dict(self, now: float) -> dict:
        idle = now - (self.last_change or self.started or now)
        return {
            "id": self.id,
            "label": self.label,
            "optional": self.optional,
            "status": self.status,
            "progress": round(self.progress, 4),
            "detail": self.detail,
            "estimated": self.estimated,
            "hint": self.hint,
            "elapsed": round((self.finished or now) - self.started, 1) if self.started else None,
            "stalled": self.status == "active" and idle > max(STALL_AFTER, 3 * (self.expected or 0)),
        }


@dataclass
class Job(Steps):
    id: str
    kind: str
    project_id: str | None
    status: str = "queued"
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result: Path | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    cancel_requested: bool = False
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        now = time.time()
        return {
            "id": self.id,
            "kind": self.kind,
            "project_id": self.project_id,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "has_result": self.result is not None and self.result.exists(),
            "filename": self.result.name if self.result else None,
            "created": self.created,
            "elapsed": round((self.finished or now) - self.created, 1),
            "steps": [s.to_dict(now) for s in self.steps],
        }

    # ---------- steps ----------

    def _step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def begin(self, step_id, detail="", *, expected=None, hint=None):
        step = self._step(step_id)
        if not step:
            return
        now = time.time()
        step.status, step.progress, step.detail, step.estimated = "active", 0.0, detail, False
        step.expected, step.hint = expected, hint
        step.started, step.finished, step.last_change = now, None, now
        self._sync()

    def update(self, step_id, progress, detail=None, *, estimated=False, heartbeat=False):
        step = self._step(step_id)
        if not step:
            return
        if step.status != "active":
            self.begin(step_id)
        progress = max(0.0, min(1.0, progress))
        if heartbeat or (not estimated and progress > step.progress):
            step.last_change = time.time()
        step.progress, step.estimated = progress, estimated
        if detail is not None:
            step.detail = detail
        self._sync()

    def done(self, step_id, detail=None):
        step = self._step(step_id)
        if not step:
            return
        now = time.time()
        step.status, step.progress, step.estimated = "done", 1.0, False
        step.started = step.started or now
        step.finished = now
        if detail is not None:
            step.detail = detail
        self._sync()

    def skip(self, step_id, detail=""):
        step = self._step(step_id)
        if not step:
            return
        step.status, step.detail, step.estimated = "skipped", detail, False
        step.started = step.finished = None
        self._sync()

    def end_active(self, status: str) -> None:
        now = time.time()
        for step in self.steps:
            if step.status == "active":
                step.status, step.finished = status, now

    def _sync(self) -> None:
        active = next((s for s in self.steps if s.status == "active"), None)
        if active:
            self.message = f"{active.label}… {active.detail}".strip()
        counted = [s for s in self.steps if s.status != "skipped"]
        if counted:
            self.progress = sum(s.progress for s in counted) / len(counted)


class JobCancelled(Exception):
    pass


class JobManager:
    """Runs long tasks in background threads. Transcriptions run one at a time."""

    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._transcribe_lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def active_for(self, project_id: str, kind: str) -> Job | None:
        for job in self.jobs.values():
            if job.project_id == project_id and job.kind == kind and job.status in ("queued", "running"):
                return job
        return None

    def create(self, kind: str, project_id: str | None, steps: list[Step] | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, project_id=project_id, steps=steps or [])
        self.jobs[job.id] = job
        return job

    def submit(self, kind: str, project_id: str | None, fn: Callable[[Job], Path | None]) -> Job:
        return self.start(self.create(kind, project_id), fn)

    def start(self, job: Job, fn: Callable[[Job], Path | None]) -> Job:
        def run():
            lock = self._transcribe_lock if job.kind == "transcribe" else None
            acquired = False
            try:
                if lock:
                    if lock.acquire(blocking=False):
                        job.skip("queue")
                    else:
                        job.begin("queue", "Another transcription is running")
                        job.message = "Waiting for another transcription to finish…"
                        while not lock.acquire(timeout=0.5):
                            if job.cancel_requested:
                                raise JobCancelled()
                        job.done("queue")
                    acquired = True
                if job.cancel_requested:
                    raise JobCancelled()
                job.status = "running"
                job.result = fn(job)
                job.progress = 1.0
                job.status = "done"
            except Exception as exc:
                if job.cancel_requested or isinstance(exc, JobCancelled) or type(exc).__name__.endswith("Cancelled"):
                    job.status = "cancelled"
                    job.message = "Cancelled"
                    job.end_active("cancelled")
                else:
                    job.status = "error"
                    job.error = str(exc) or type(exc).__name__
                    job.end_active("error")
                    traceback.print_exc()
            finally:
                job.finished = time.time()
                if acquired:
                    lock.release()

        threading.Thread(target=run, daemon=True, name=f"job-{job.kind}-{job.id}").start()
        return job

    def cancel(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.cancel_requested = True
        return job
