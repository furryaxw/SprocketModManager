import threading
import unittest
from pathlib import Path

from sprocket_mod_manager.application.install_queue import (
    CANCELED,
    COMPLETED,
    FAILED,
    INSTALLING,
    InstallQueue,
)
from sprocket_mod_manager.domain.errors import InstallConflictError


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

    def test_a_second_task_for_one_package_replaces_its_row(self):
        """一个模组只占一行：跑完之后再入队，占用的还是那一行。"""
        queue = InstallQueue(lambda _entry, _progress: None)
        try:
            first, = queue.enqueue(["test.mod"], Path("game"))
            self.assertTrue(queue.wait_until_idle(1))

            second, = queue.enqueue(["test.mod"], Path("game"))
            rows = queue.snapshot()
            self.assertEqual(len(rows), 1, "同一个模组不许并排留两行")
            self.assertEqual(rows[0].task_id, second.task_id)
            self.assertNotEqual(rows[0].task_id, first.task_id)
            self.assertTrue(queue.wait_until_idle(1))
        finally:
            queue.close()

    def test_a_new_task_replaces_a_waiting_one(self):
        started = threading.Event()
        release = threading.Event()

        def runner(entry, _progress):
            if entry.package_id == "test.blocker":
                started.set()
                release.wait(2)

        queue = InstallQueue(runner)
        try:
            queue.enqueue(["test.blocker", "test.mod"], Path("game"))
            self.assertTrue(started.wait(1))

            replaced, = queue.enqueue(["test.mod"], Path("game"), force_conflicts=True)
            rows = [entry for entry in queue.snapshot() if entry.package_id == "test.mod"]
            self.assertEqual([entry.task_id for entry in rows], [replaced.task_id],
                             "等待中的旧任务被新任务顶掉，不留第二行")
            self.assertTrue(replaced.force_conflicts)
            release.set()
            self.assertTrue(queue.wait_until_idle(2))
        finally:
            release.set()
            queue.close()

    def test_clear_completed_keeps_failed_rows(self):
        def runner(entry, _progress):
            if entry.package_id == "test.bad":
                raise RuntimeError("download failed")

        queue = InstallQueue(runner)
        try:
            queue.enqueue(["test.bad", "test.good"], Path("game"))
            self.assertTrue(queue.wait_until_idle(2))
            queue.clear_completed()
            states = {entry.package_id: entry.state for entry in queue.snapshot()}
        finally:
            queue.close()

        self.assertEqual(states, {"test.bad": FAILED}, "失败的那行要留着，界面还要拿它重试")

    def test_duplicate_active_package_is_enqueued_once(self):
        started = threading.Event()
        release = threading.Event()

        def runner(_entry, _progress):
            started.set()
            release.wait(2)

        queue = InstallQueue(runner)
        try:
            added = queue.enqueue(["test.mod", "test.mod"], Path("game"))
            self.assertTrue(started.wait(1), "第一条要真的开始装")
            duplicate = queue.enqueue(["test.mod"], Path("game"))
            self.assertEqual(len(added), 1)
            self.assertEqual(duplicate, (), "正在装的那一行不能被顶掉")
            self.assertEqual(len(queue.snapshot()), 1)
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

    def test_conflict_error_code_is_exposed_for_force_retry(self):
        def runner(_entry, _progress):
            raise InstallConflictError("unmanaged file already exists")

        queue = InstallQueue(runner)
        try:
            queue.enqueue(["test.mod"], Path("game"))
            self.assertTrue(queue.wait_until_idle(1))
            entry = queue.snapshot()[0]
        finally:
            queue.close()

        self.assertEqual(entry.state, FAILED)
        self.assertEqual(entry.error_code, "file_conflict")

    def test_force_conflicts_flag_reaches_runner(self):
        seen = []
        queue = InstallQueue(lambda entry, _progress: seen.append(entry.force_conflicts))
        try:
            queue.enqueue(["test.mod"], Path("game"), force_conflicts=True)
            self.assertTrue(queue.wait_until_idle(1))
        finally:
            queue.close()

        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
