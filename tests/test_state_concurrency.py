"""安装记录只有一份，进程里所有读写都要串行。

真机上的样子是「数据层在刷新已安装读数（对账会落盘）的同时，安装器正在写同一份 installed.json」——
Windows 上并发读句柄会让原子替换报访问被拒。这里钉住三件事：同一个路径只有一把锁、一次变更的
读改写整段握着它、并发读写在压力下不撕裂文件。
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.infrastructure.installer import Installer
from sprocket_mod_manager.infrastructure.manager_paths import STATE_FILE_NAME, state_file_path
from sprocket_mod_manager.infrastructure.profiles import InstallerProfiles
from sprocket_mod_manager.infrastructure.state import StateStore, state_lock
from sprocket_mod_manager.utilities.checksums import sha256_file

PACKAGE = "test.recorded-mod"
PAYLOAD = "Mods/Installed.dll"


def seeded_state(digest: str) -> dict:
    return {
        "schema_version": 2,
        "packages": {
            PACKAGE: {
                "id": PACKAGE,
                "name": "Installed Mod",
                "version": "1.0.0",
                "requested": True,
                "dependencies": [],
                "files": [PAYLOAD],
            }
        },
        "files": {PAYLOAD: {"sha256": digest, "owners": [PACKAGE], "disabled": False}},
        "metadata": {},
    }


class StateLockTests(unittest.TestCase):
    def setUp(self) -> None:
        running = patch(
            "sprocket_mod_manager.infrastructure.installer.sprocket_is_running",
            return_value=False,
        )
        running.start()
        self.addCleanup(running.stop)
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.game = self.root / "game"
        (self.game / "Mods").mkdir(parents=True)
        (self.game / "Sprocket.exe").touch()
        (self.game / PAYLOAD).write_bytes(b"payload")
        self.digest = sha256_file(self.game / PAYLOAD)

    def test_every_store_for_one_file_shares_one_lock(self) -> None:
        first = StateStore(state_file_path(self.game))
        second = StateStore(state_file_path(self.game))

        self.assertIs(first.lock, second.lock, "按路径发锁：按对象发锁等于没有锁")
        self.assertIs(first.lock, state_lock(state_file_path(self.game)))
        self.assertIsNot(first.lock, state_lock(self.root / "other" / STATE_FILE_NAME))

    def test_the_installer_is_reused_for_one_game_directory(self) -> None:
        profiles = InstallerProfiles(self.root / "app")

        first = profiles.installer_for(self.game)
        second = profiles.installer_for(self.game)

        self.assertIs(first, second)
        self.assertIs(first.state_store.lock, second.state_store.lock)

    def test_a_mutation_holds_the_lock_for_its_whole_sequence(self) -> None:
        """变更是读改写：整段互斥，别的读写只能等它做完。"""
        installer = Installer(self.root / "app", StateStore(state_file_path(self.game)))
        installer.state_store.save(seeded_state(self.digest))
        finished = threading.Event()
        result: list[object] = []

        def remove() -> None:
            try:
                result.append(installer.remove_many([PACKAGE], self.game))
            except Exception as exc:  # noqa: BLE001 - 探针要把失败带回来
                result.append(exc)
            finally:
                finished.set()

        with installer.state_store.lock:
            worker = threading.Thread(target=remove, name="remove-under-lock")
            worker.start()
            self.assertFalse(
                finished.wait(0.5), "锁被握着时，卸载不该插进来读改写"
            )
        worker.join(10)

        self.assertTrue(finished.is_set(), "松开锁之后卸载要能做完")
        self.assertEqual(result, [([PACKAGE], [])], result)
        self.assertEqual(installer.state_store.load()["packages"], {})

    def test_concurrent_reads_and_writes_never_tear_the_file(self) -> None:
        store = StateStore(state_file_path(self.game))
        store.save(seeded_state(self.digest))
        errors: list[BaseException] = []
        start = threading.Event()

        def reader() -> None:
            start.wait()
            for _ in range(60):
                try:
                    state = store.load()
                    self.assertIn("packages", state)
                except BaseException as exc:  # noqa: BLE001 - 线程里的失败要带回主线程
                    errors.append(exc)
                    return

        def writer() -> None:
            start.wait()
            for _ in range(60):
                try:
                    store.save(seeded_state(self.digest))
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)
                    return

        workers = [
            *(threading.Thread(target=reader, name=f"reader-{index}") for index in range(4)),
            *(threading.Thread(target=writer, name=f"writer-{index}") for index in range(2)),
        ]
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(30)

        self.assertEqual(errors, [], "并发读写不该有异常")
        state = json.loads(state_file_path(self.game).read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], 2)
        self.assertIn(PACKAGE, state["packages"])
        self.assertFalse(
            state_file_path(self.game).with_suffix(".json.tmp").exists(), "不留临时文件"
        )

    def test_a_reading_and_a_removal_do_not_collide(self) -> None:
        """读数那条路会先对账、必要时落盘 —— 它和卸载必须能同时在跑。"""
        installer = Installer(self.root / "app", StateStore(state_file_path(self.game)))
        installer.state_store.save(seeded_state(self.digest))
        errors: list[BaseException] = []
        start = threading.Event()

        def read() -> None:
            start.wait()
            for _ in range(80):
                try:
                    installer.reconcile(self.game)
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)
                    return

        readers = [threading.Thread(target=read, name=f"read-{index}") for index in range(4)]
        for reader in readers:
            reader.start()
        start.set()
        time.sleep(0.05)

        installer.remove_many([PACKAGE], self.game)
        for reader in readers:
            reader.join(30)

        self.assertEqual(errors, [], "读数与卸载不该互相踩")
        self.assertEqual(installer.state_store.load()["packages"], {})


if __name__ == "__main__":
    unittest.main()
