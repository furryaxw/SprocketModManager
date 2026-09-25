import json
import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.domain.errors import InstallError
from sprocket_mod_manager.infrastructure.state import StateStore


class StateStoreTests(unittest.TestCase):
    def write_state(self, root: Path, state: object) -> StateStore:
        store = StateStore(root / "installed.json")
        store.path.write_text(json.dumps(state), encoding="utf-8")
        return store

    def test_load_sanitizes_invalid_relationship_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.write_state(
                Path(temporary),
                {
                    "schema_version": 1,
                    "packages": {
                        "test.mod": {
                            "files": [None, "", 42, "Mods/TestMod.dll"],
                            "dependencies": [None, "", 42, "test.lib"],
                        }
                    },
                    "files": {
                        "": {"owners": ["test.mod"]},
                        "Mods/Broken.dll": None,
                        "Mods/TestMod.dll": {
                            "owners": [None, "", 42, "test.mod"],
                            "sha256": "test",
                            "preexisting": False,
                        },
                    },
                },
            )

            state = store.load()

        self.assertEqual(
            state["packages"]["test.mod"]["files"],
            ["Mods/TestMod.dll"],
        )
        self.assertEqual(
            state["packages"]["test.mod"]["dependencies"],
            ["test.lib"],
        )
        self.assertEqual(
            state["files"]["Mods/TestMod.dll"]["owners"],
            ["test.mod"],
        )
        self.assertEqual(list(state["files"]), ["Mods/TestMod.dll"])

    def test_load_treats_null_relationship_lists_as_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.write_state(
                Path(temporary),
                {
                    "schema_version": 1,
                    "packages": {
                        "test.mod": {"files": None, "dependencies": None}
                    },
                    "files": {
                        "Mods/TestMod.dll": {"owners": None}
                    },
                },
            )

            state = store.load()

        self.assertEqual(state["packages"]["test.mod"]["files"], [])
        self.assertEqual(state["packages"]["test.mod"]["dependencies"], [])
        self.assertEqual(state["files"]["Mods/TestMod.dll"]["owners"], [])

    def test_load_rejects_non_list_relationship_fields(self):
        cases = (
            ({"files": "Mods/TestMod.dll", "dependencies": []}, {}),
            ({"files": [], "dependencies": "test.lib"}, {}),
            (
                {"files": [], "dependencies": []},
                {"Mods/TestMod.dll": {"owners": "test.mod"}},
            ),
        )
        for package, files in cases:
            with self.subTest(package=package, files=files):
                with tempfile.TemporaryDirectory() as temporary:
                    store = self.write_state(
                        Path(temporary),
                        {
                            "schema_version": 1,
                            "packages": {"test.mod": package},
                            "files": files,
                        },
                    )
                    with self.assertRaisesRegex(
                        InstallError,
                        "installed state is malformed",
                    ):
                        store.load()

    def test_load_normalises_a_modloader_file_list_away(self):
        """基础运行时的记录只留版本与目录：加载时把逐文件清单摘掉，包记录本身留着。"""
        with tempfile.TemporaryDirectory() as temporary:
            store = self.write_state(
                Path(temporary),
                {
                    "schema_version": 2,
                    "packages": {
                        "lavagang.melonloader": {
                            "name": "MelonLoader",
                            "repository": "LavaGang/MelonLoader",
                            "version": "0.7.3",
                            "requested": True,
                            "install_mode": "standard",
                            "dependencies": [],
                            "kind": "modloader",
                            "directories": ["MelonLoader"],
                            "payload_files": [{"path": "version.dll", "sha256": "def"}],
                            "files": [
                                {
                                    "path": "MelonLoader/net6/MelonLoader.dll",
                                    "sha256": "abc",
                                    "disabled": False,
                                }
                            ],
                        }
                    },
                    "unowned": {},
                },
            )

            state = store.load()

            self.assertEqual(state["packages"]["lavagang.melonloader"]["files"], [])
            self.assertEqual(state["files"], {}, "逐文件条目在加载时就摘干净")
            self.assertEqual(state["packages"]["lavagang.melonloader"]["version"], "0.7.3")
            self.assertEqual(
                state["packages"]["lavagang.melonloader"]["payload_files"],
                [{"path": "version.dll", "sha256": "def"}],
                "顶层文件条目保留",
            )
            store.save(state)
            raw = store.path.read_text(encoding="utf-8")
            self.assertIn("lavagang.melonloader", raw, "包记录必须活下来")
            self.assertIn("version.dll", raw, "顶层文件条目要落盘")
            self.assertNotIn("MelonLoader/net6/MelonLoader.dll", raw)

    def test_load_normalises_a_modloader_record_that_has_no_kind_field(self):
        """既有记录不带 `kind`：调用方按包 id 报出加载器时同样归一。"""
        with tempfile.TemporaryDirectory() as temporary:
            store = self.write_state(
                Path(temporary),
                {
                    "schema_version": 2,
                    "packages": {
                        "lavagang.melonloader": {
                            "name": "MelonLoader",
                            "repository": "LavaGang/MelonLoader",
                            "version": "0.7.3",
                            "requested": True,
                            "install_mode": "standard",
                            "dependencies": [],
                            "directories": ["MelonLoader"],
                            "files": [
                                {
                                    "path": "MelonLoader/net6/MelonLoader.dll",
                                    "sha256": "abc",
                                    "disabled": False,
                                }
                            ],
                        }
                    },
                    "unowned": {},
                },
            )

            state = store.load(modloaders={"lavagang.melonloader"})

            record = state["packages"]["lavagang.melonloader"]
            self.assertEqual(record["kind"], "modloader")
            self.assertEqual(record["files"], [])
            self.assertEqual(state["files"], {})

    def test_load_rejects_non_object_state_and_package_records(self):
        cases = (
            None,
            [],
            {
                "schema_version": 1,
                "packages": {"test.mod": None},
                "files": {},
            },
        )
        for state in cases:
            with self.subTest(state=state):
                with tempfile.TemporaryDirectory() as temporary:
                    store = self.write_state(Path(temporary), state)
                    with self.assertRaisesRegex(
                        InstallError,
                        "installed state is malformed",
                    ):
                        store.load()


if __name__ == "__main__":
    unittest.main()
