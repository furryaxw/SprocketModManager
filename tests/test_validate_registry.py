"""环境表在工作树上写坏时，CI 的门禁要直接红，而不是安静地少过滤一条。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN_INDEX = load("sprocket_gen_index", "gen-index.py")
VALIDATE = load("sprocket_validate_registry", "validate_registry.py")


def table_module(path: Path):
    """Stand-in for gen-index.py with ENVIRONMENT_FILE pointed at `path`."""

    class Module:
        ENVIRONMENT_FILE = path
        load_environment_table = staticmethod(GEN_INDEX.load_environment_table)

    return Module


class EnvironmentTableGateTests(unittest.TestCase):
    def write(self, directory: str, payload) -> Path:
        path = Path(directory) / GEN_INDEX.ENVIRONMENT_FILE_NAME
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_the_shipped_table_passes(self) -> None:
        self.assertEqual(VALIDATE.validate_environment(GEN_INDEX), [])

    def test_a_missing_table_is_a_failure(self) -> None:
        errors = VALIDATE.validate_environment(table_module(Path("nowhere") / "environment.json"))

        self.assertEqual(errors, ["environment.json is missing"])

    def test_a_broken_entry_fails_with_the_warning_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {"entries": [{"melonloader": ">=0.7.0", "sprocket": "0.2.53"}]})

            errors = VALIDATE.validate_environment(table_module(path))

        self.assertEqual(len(errors), 2)
        self.assertIn("第 1 条", errors[0])
        self.assertIn("has no usable entry", errors[1])

    def test_a_table_without_a_usable_entry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {"entries": []})

            errors = VALIDATE.validate_environment(table_module(path))

        self.assertIn("environment.json has no usable entry", errors)
