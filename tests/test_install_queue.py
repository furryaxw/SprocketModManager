import threading
import unittest
from pathlib import Path

from sprocket_mod_manager.install_queue import (
    CANCELED,
    COMPLETED,
    FAILED,
    INSTALLING,
    InstallQueue,
)


class InstallQueueTests(unittest.TestCase):
    def test_failed_item_does_not_block_later_installs(self):
        calls = []

        def runner(entry, progress):
            calls.append(entry.package_id)
            progress(f"installing {entry.package_id}")
            if entry.package_id == "test.bad":
                raise RuntimeError("download failed")

        queue = InstallQueue(runner)
        try:
            queue.enqueue(["test.bad", "test.good"], Path("game"))
            self.assertTrue(queue.wait_until_idle(2))
            states = {entry.package_id: entry.state for entry in queue.snapshot()}
            self.assertEqual(calls, ["test.bad", "test.good"])
            self.assertEqual(states, {"test.bad": FAILED, "test.good": COMPLETED})
        finally:
            queue.close()

    def test_duplicate_active_package_is_enqueued_once(self):
        release = threading.Event()

        def runner(_entry, _progress):
            release.wait(2)

        queue = InstallQueue(runner)
        try:
            added = queue.enqueue(["test.mod", "test.mod"], Path("game"))
            duplicate = queue.enqueue(["test.mod"], Path("game"))
            self.assertEqual(len(added), 1)
            self.assertEqual(duplicate, ())
            release.set()
            self.assertTrue(queue.wait_until_idle(2))
        finally:
            release.set()
            queue.close()

    def test_waiting_item_can_be_canceled(self):
        started = threading.Event()
        release = threading.Event()

        def runner(entry, _progress):
            if entry.package_id == "test.first":
                started.set()
                release.wait(2)

        queue = InstallQueue(runner)
        try:
            first, second = queue.enqueue(["test.first", "test.second"], Path("game"))
            self.assertTrue(started.wait(1))
            self.assertEqual(
                next(entry.state for entry in queue.snapshot() if entry.task_id == first.task_id),
                INSTALLING,
            )
            self.assertTrue(queue.cancel(second.task_id))
            release.set()
            self.assertTrue(queue.wait_until_idle(2))
            states = {entry.task_id: entry.state for entry in queue.snapshot()}
            self.assertEqual(states[second.task_id], CANCELED)
        finally:
            release.set()
            queue.close()

    def test_close_reports_running_install_until_transaction_finishes(self):
        started = threading.Event()
        release = threading.Event()

        def runner(_entry, _progress):
            started.set()
            release.wait(2)

        queue = InstallQueue(runner)
        queue.enqueue(["test.mod"], Path("game"))
        self.assertTrue(started.wait(1))

        self.assertTrue(queue.is_installing())
        self.assertFalse(queue.close(timeout=0.01))
        release.set()
        self.assertTrue(queue.close(timeout=1))

    def test_closed_queue_rejects_new_entries(self):
        queue = InstallQueue(lambda _entry, _progress: None)
        self.assertTrue(queue.close(timeout=1))

        with self.assertRaisesRegex(RuntimeError, "queue is closed"):
            queue.enqueue(["test.mod"], Path("game"))

    def test_entry_keeps_the_enqueue_context(self):
        context = object()
        seen = []
        queue = InstallQueue(lambda entry, _progress: seen.append(entry.context))
        try:
            queue.enqueue(["test.mod"], Path("game"), context=context)
            self.assertTrue(queue.wait_until_idle(1))
        finally:
            queue.close()

        self.assertEqual(seen, [context])


if __name__ == "__main__":
    unittest.main()
