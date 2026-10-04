import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Job:
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

    def to_dict(self) -> dict:
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
            "elapsed": round((self.finished or time.time()) - self.created, 1),
        }


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

    def submit(self, kind: str, project_id: str | None, fn: Callable[[Job], Path | None]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, project_id=project_id)
        self.jobs[job.id] = job

        def run():
            lock = self._transcribe_lock if kind == "transcribe" else None
            if lock:
                job.message = "Waiting for another transcription to finish…"
                lock.acquire()
            try:
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
                else:
                    job.status = "error"
                    job.error = str(exc) or type(exc).__name__
                    traceback.print_exc()
            finally:
                job.finished = time.time()
                if lock:
                    lock.release()

        threading.Thread(target=run, daemon=True, name=f"job-{kind}-{job.id}").start()
        return job

    def cancel(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.cancel_requested = True
        return job
