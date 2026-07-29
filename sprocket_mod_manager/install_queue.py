from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable


WAITING = "waiting"
INSTALLING = "installing"
COMPLETED = "completed"
FAILED = "failed"
CANCELED = "canceled"
ACTIVE_STATES = {WAITING, INSTALLING}
FINISHED_STATES = {COMPLETED, FAILED, CANCELED}


@dataclass
class InstallQueueEntry:
    task_id: str
    package_id: str
    game_path: Path
    state: str = WAITING
    message: str = ""
    context: object | None = None


QueueRunner = Callable[[InstallQueueEntry, Callable[[str], None]], None]
QueueListener = Callable[[tuple[InstallQueueEntry, ...]], None]


class InstallQueue:
    def __init__(self, runner: QueueRunner, listener: QueueListener | None = None):
        self._runner = runner
        self._listener = listener
        self._entries: list[InstallQueueEntry] = []
        self._condition = threading.Condition()
        self._closed = False
        self._worker = threading.Thread(target=self._work, name="sprocket-install-queue", daemon=True)
        self._worker.start()

    def enqueue(
        self,
        package_ids: Iterable[str],
        game_path: Path,
        *,
        context: object | None = None,
    ) -> tuple[InstallQueueEntry, ...]:
        added: list[InstallQueueEntry] = []
        with self._condition:
            if self._closed:
                raise RuntimeError("install queue is closed")
            active_ids = {
                entry.package_id
                for entry in self._entries
                if entry.state in ACTIVE_STATES
            }
            for package_id in dict.fromkeys(package_ids):
                if package_id in active_ids:
                    continue
                entry = InstallQueueEntry(
                    uuid.uuid4().hex,
                    package_id,
                    game_path,
                    context=context,
                )
                self._entries.append(entry)
                active_ids.add(package_id)
                added.append(entry)
            self._condition.notify_all()
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return tuple(replace(entry) for entry in added)

    def cancel(self, task_id: str) -> bool:
        with self._condition:
            entry = next((item for item in self._entries if item.task_id == task_id), None)
            if entry is None or entry.state != WAITING:
                return False
            entry.state = CANCELED
            entry.message = ""
            self._condition.notify_all()
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return True

    def clear_finished(self) -> None:
        with self._condition:
            self._entries = [entry for entry in self._entries if entry.state not in FINISHED_STATES]
            snapshot = self._snapshot_locked()
        self._notify(snapshot)

    def snapshot(self) -> tuple[InstallQueueEntry, ...]:
        with self._condition:
            return self._snapshot_locked()

    def wait_until_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while any(entry.state in ACTIVE_STATES for entry in self._entries):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def is_installing(self) -> bool:
        with self._condition:
            return any(entry.state == INSTALLING for entry in self._entries)

    def close(self, timeout: float | None = 5.0) -> bool:
        with self._condition:
            self._closed = True
            for entry in self._entries:
                if entry.state == WAITING:
                    entry.state = CANCELED
            self._condition.notify_all()
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        if threading.current_thread() is self._worker:
            return False
        self._worker.join(timeout)
        return not self._worker.is_alive()

    def _work(self) -> None:
        while True:
            with self._condition:
                entry = next((item for item in self._entries if item.state == WAITING), None)
                while entry is None and not self._closed:
                    self._condition.wait()
                    entry = next((item for item in self._entries if item.state == WAITING), None)
                if entry is None and self._closed:
                    return
                assert entry is not None
                entry.state = INSTALLING
                entry.message = ""
                snapshot = self._snapshot_locked()
            self._notify(snapshot)

            def progress(message: str) -> None:
                with self._condition:
                    if entry.state != INSTALLING:
                        return
                    entry.message = message
                    progress_snapshot = self._snapshot_locked()
                self._notify(progress_snapshot)

            try:
                self._runner(replace(entry), progress)
            except Exception as exc:
                with self._condition:
                    entry.state = FAILED
                    entry.message = str(exc)
                    self._condition.notify_all()
                    snapshot = self._snapshot_locked()
            else:
                with self._condition:
                    entry.state = COMPLETED
                    entry.message = ""
                    self._condition.notify_all()
                    snapshot = self._snapshot_locked()
            self._notify(snapshot)

    def _snapshot_locked(self) -> tuple[InstallQueueEntry, ...]:
        return tuple(replace(entry) for entry in self._entries)

    def _notify(self, snapshot: tuple[InstallQueueEntry, ...]) -> None:
        if self._listener is None:
            return
        try:
            self._listener(snapshot)
        except Exception:
            pass
