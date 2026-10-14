"""打包工具：同一份输入产出逐字节相同的 zip，清单原样带走，条目骨架过得了注册表校验。"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MAKE = load("sprocket_make_pack", "make_pack.py")
GEN_INDEX = load("sprocket_gen_index_make", "gen-index.py")

PACK_ID = "example.realism-pack"


def manifest_bytes(*, members: bool = True, install: bool = True) -> bytes:
    document: dict = {"schema_version": 1}
    if members:
        document["members"] = [{"id": "example.member-a", "version": ">=1.0.0", "when": "*"}]
    if install:
        document["install"] = {
            "mode": "patch",
            "files": [{"match": "Parts/**", "type": "sprocket:streamingassets", "subpath": "Parts"}],
            "exclude": [],
        }
    return json.dumps(document, indent=2).encode("utf-8")


class MakePackTests(unittest.TestCase):
    def build(self, pack_dir: Path, out_dir: Path, **overrides):
        arguments = {
            "package_id": PACK_ID,
            "name": "RealismPack",
            "repository": "ExampleAuthor/RealismPack",
            "authors": ["ExampleAuthor"],
            "license_text": "MIT",
            "version": "1.0.0",
        }
        arguments.update(overrides)
        return MAKE.build(pack_dir, out_dir, **arguments)

    def write_pack(self, root: Path, *, overrides: bool = True, manifest: bytes | None = None) -> Path:
        pack_dir = root / "my-pack"
        (pack_dir).mkdir(parents=True, exist_ok=True)
        (pack_dir / "pack.json").write_bytes(manifest or manifest_bytes())
        if overrides:
            (pack_dir / "overrides" / "Parts").mkdir(parents=True)
            (pack_dir / "overrides" / "Parts" / "foo.json").write_bytes(b'{"part": "override"}')
            (pack_dir / "overrides" / "readme.txt").write_bytes(b"notes")
        return pack_dir

    def test_the_payload_is_a_deterministic_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack_dir = self.write_pack(root)

            first = self.build(pack_dir, root / "one")
            second = self.build(pack_dir, root / "two")

        self.assertEqual(first["payload"], second["payload"], "同一份输入必须产出同样的字节")
        with zipfile.ZipFile(io.BytesIO(first["payload"])) as archive:
            self.assertEqual(
                sorted(archive.namelist()), ["Parts/foo.json", "readme.txt"]
            )
            self.assertEqual(archive.read("Parts/foo.json"), b'{"part": "override"}')

    def test_the_manifest_is_copied_verbatim(self):
        raw = manifest_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack_dir = self.write_pack(root, manifest=raw)

            self.build(pack_dir, root / "out")

            self.assertEqual((root / "out" / "pack.json").read_bytes(), raw)

    def test_the_entry_skeleton_passes_registry_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack_dir = self.write_pack(root)

            result = self.build(pack_dir, root / "out")

            GEN_INDEX.validate_meta(result["entry"], PACK_ID)
            self.assertEqual(result["entry"]["kind"], "pack")
            self.assertEqual(result["entry"]["category"], "pack")
            self.assertNotIn("install", result["entry"])
            self.assertEqual(
                result["entry"]["release"]["assets"]["include"], ["RealismPack-*.zip", "pack.json"]
            )

    def test_a_members_only_pack_has_no_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack_dir = self.write_pack(root, overrides=False, manifest=manifest_bytes(install=False))

            result = self.build(pack_dir, root / "out")

            self.assertIsNone(result["payload"])
            self.assertEqual(result["payload_name"], "")
            self.assertEqual(result["entry"]["release"]["assets"]["include"], ["pack.json"])
            self.assertEqual(sorted(path.name for path in (root / "out").iterdir()),
                             ["entry.json", "pack.json"])

    def test_a_registered_member_passes_the_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack_dir = self.write_pack(root)
            registry = root / "packages" / "example"
            registry.mkdir(parents=True)
            (registry / "member-a.json").write_text("{}", encoding="utf-8")
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                self.build(pack_dir, root / "out", packages_dir=root / "packages")

        self.assertEqual(stderr.getvalue(), "", "成员在注册表里就不该有警告")

    def test_an_unregistered_member_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack_dir = self.write_pack(root)
            (root / "packages").mkdir()
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                self.build(pack_dir, root / "out", packages_dir=root / "packages")

        self.assertIn("member is not registered yet: example.member-a", stderr.getvalue())

    def test_a_missing_manifest_stops_the_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty").mkdir()

            with self.assertRaises(SystemExit):
                self.build(root / "empty", root / "out")


if __name__ == "__main__":
    unittest.main()
