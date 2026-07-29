import json
import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.errors import InstallError
from sprocket_mod_manager.state import StateStore


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
