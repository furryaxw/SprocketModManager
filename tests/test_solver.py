import unittest

from sprocket_mod_manager.models import RegistryPackage, ReleaseAsset, ReleaseInfo
from sprocket_mod_manager.registry import Registry
from sprocket_mod_manager.semver import Version
from sprocket_mod_manager.solver import DependencySolver


def package(package_id, dependencies=()):
    return RegistryPackage(
        id=package_id,
        name=package_id.rsplit(".", 1)[-1],
        authors=("test",),
        repository="test/repo",
        license="MIT",
        display_name={"en": package_id},
        description={"en": "test"},
        release={"assets": {"include": ["*.dll"], "exclude": []}},
        dependencies=tuple(dependencies),
        install={"scan_dlls": True, "exclude": [], "overrides": []},
        category="library",
        tags=(),
    )


def release(version):
    return ReleaseInfo(
        id=hash(version) & 0xFFFF,
        tag=f"v{version}",
        version=Version.parse(version),
        prerelease=False,
        published_at="",
        assets=(ReleaseAsset(1, "package.dll", 1, "https://github.com/test/repo/releases/download/v/package.dll"),),
    )


class FakeGitHub:
    def __init__(self, releases):
        self.mapping = releases

    def releases(self, pkg):
        return self.mapping[pkg.id]

    @staticmethod
    def install_assets(pkg, item):
        return item.assets


class SolverTests(unittest.TestCase):
    def test_chooses_highest_compatible_dependency(self):
        root = package(
            "test.root",
            ({"id": "test.lib", "version": ">=1.0.0 <2.0.0", "when": "*"},),
        )
        lib = package("test.lib")
        github = FakeGitHub(
            {
                root.id: (release("1.0.0"),),
                lib.id: (release("2.0.0"), release("1.5.0"), release("1.0.0")),
            }
        )
        plan = DependencySolver(Registry([root, lib]), github).resolve(root.id)
        self.assertEqual([item.package.id for item in plan.packages], [lib.id, root.id])
        self.assertEqual(str(plan.packages[0].release.version), "1.5.0")

    def test_backtracks_when_latest_root_has_unsatisfied_dependency(self):
        root = package(
            "test.root",
            (
                {"id": "test.lib", "version": ">=2.0.0", "when": ">=2.0.0"},
                {"id": "test.lib", "version": "<2.0.0", "when": "<2.0.0"},
            ),
        )
        lib = package("test.lib")
        github = FakeGitHub(
            {
                root.id: (release("2.0.0"), release("1.0.0")),
                lib.id: (release("1.4.0"),),
            }
        )
        plan = DependencySolver(Registry([root, lib]), github).resolve(root.id)
        by_id = plan.by_id()
        self.assertEqual(str(by_id[root.id].release.version), "1.0.0")
        self.assertEqual(str(by_id[lib.id].release.version), "1.4.0")


if __name__ == "__main__":
    unittest.main()
