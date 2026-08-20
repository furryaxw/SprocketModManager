from __future__ import annotations

import argparse
import importlib.util
import io
import sys
import threading
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sprocket_mod_manager.presentation.web_gui import ClientApi


def load_access_server(source_root: Path):
    source = source_root.resolve() / "access_server.py"
    if not source.is_file():
        raise FileNotFoundError(f"access server source not found: {source}")
    spec = importlib.util.spec_from_file_location("sprocket_test_access_server", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load access server source: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(access_server_root: Path) -> None:
    access_server = load_access_server(access_server_root)
    with TemporaryDirectory(prefix="sprocket-private-integration-") as directory:
        root = Path(directory)
        database = root / "server" / "access.db"
        store = access_server.AccessStore(database, b"integration-test-pepper")

        dll = b"isolated private integration dll"
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("PrivateIntegration.dll", dll)
        archive_bytes = archive_buffer.getvalue()
        uploaded = store.store_archive(
            io.BytesIO(archive_bytes),
            len(archive_bytes),
            "PrivateIntegration.zip",
        )
        store.create_package({
            "id": "test.private-integration",
            "name": "PrivateIntegration",
            "version": "1.0.0",
            "description": "isolated integration package",
            "required_permission": "download:test.private-integration",
            "install": {
                "scan_dlls": False,
                "exclude": [],
                "overrides": [{"match": "PrivateIntegration.dll", "target": "Mods"}],
            },
            "archive_name": uploaded["name"],
            "archive_sha256": uploaded["sha256"],
            "files": [{
                "name": "PrivateIntegration.dll",
                "target": "Mods/PrivateIntegration.dll",
            }],
        })
        key = store.create_batch(
            "integration",
            1,
            ["download:test.private-integration"],
            None,
        )["keys"][0]

        server = access_server.create_server("127.0.0.1", 0, store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        api = None
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").touch()
            api = ClientApi("integration", app_dir=root / "manager")
            api.config["game_path"] = str(game)
            api.config_store.save(api.config)

            assert api.add_developer_server(url)["ok"]
            assert api.set_demo_github_login("123")["ok"]
            assert api.activate_developer_server("local-test-server", key)["ok"]
            catalog = api.get_developer_servers()
            assert catalog["ok"]
            package_id = "local-test-server:test.private-integration"
            assert [item["id"] for item in catalog["packages"]] == [package_id]
            assert api.plan_install([package_id])["ok"]

            api._install_private_package(
                package_id,
                game,
                lambda _message: None,
                force_conflicts=False,
            )
            installed = game / "Mods" / "PrivateIntegration.dll"
            assert installed.read_bytes() == dll
            assert api.remove(package_id)["ok"]
            assert not installed.exists()
            assert database.is_file()
        finally:
            if api is not None:
                api.install_queue.close()
            server.shutdown()
            thread.join()
            server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an isolated Manager/access-server private package integration test."
    )
    parser.add_argument(
        "--access-server-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "sprocket-mod-access-server",
    )
    args = parser.parse_args()
    run(args.access_server_root)
    print("Private integration test passed")


if __name__ == "__main__":
    main()
