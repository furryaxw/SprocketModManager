import unittest

from validate_registry import validate_online


class RegistryValidationTests(unittest.TestCase):
    def test_online_validation_falls_back_to_latest_when_release_list_is_empty(self):
        release = {
            "tag_name": "v1.3.1",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "TestMod.dll"}],
        }

        class Api:
            @staticmethod
            def get(path):
                if path == "/repos/example/TestMod":
                    return {
                        "private": False,
                        "archived": False,
                        "default_branch": "main",
                        "license": {"spdx_id": "GPL-3.0"},
                    }
                if path == "/repos/example/TestMod/git/trees/main?recursive=1":
                    return {
                        "truncated": False,
                        "tree": [
                            {"type": "blob", "path": "LICENSE.txt"},
                            {"type": "blob", "path": "TestMod.cs"},
                        ],
                    }
                if path == "/repos/example/TestMod/releases?per_page=100":
                    return []
                if path == "/repos/example/TestMod/releases/latest":
                    return release
                raise AssertionError(f"unexpected path: {path}")

        meta = {
            "repository": "example/TestMod",
            "release": {
                "include_prerelease": False,
                "version_pattern": r"^v?([0-9]+\.[0-9]+\.[0-9]+)$",
                "assets": {"include": ["TestMod.dll"], "exclude": []},
            },
        }

        self.assertEqual(validate_online(meta, Api()), [])


if __name__ == "__main__":
    unittest.main()
