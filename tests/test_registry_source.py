import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_mod_manager.domain.errors import RegistryError
from sprocket_mod_manager.application.service import ModManagerService
from sprocket_mod_manager.infrastructure.registry_source import PACKAGES_NAME


DOCUMENTS = {
    "/packages.json": {"schema_version": 1, "packages": []},
    "/environment.json": {"schema_version": 1, "game": {}, "providers": {}},
    "/diagnosis.json": {"schema_version": 1, "entries": []},
}
REGISTRY_PACKAGE_COUNT = len(DOCUMENTS["/packages.json"]["packages"])


class RegistryHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = DOCUMENTS.get(self.path)
        if payload is None:
            self.send_error(404)
            return
        body = json.dumps(payload).encode("utf-8")
        self.server.requested.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class RegistrySourceTests(unittest.TestCase):
    def test_loopback_http_registry_is_allowed(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), RegistryHandler)
        server.requested = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as temporary:
                service = ModManagerService(Path(temporary))
                registry = service.load_registry(f"http://127.0.0.1:{server.server_port}")
                self.assertEqual(len(registry.packages), REGISTRY_PACKAGE_COUNT)
                self.assertEqual(
                    sorted(server.requested),
                    ["/diagnosis.json", "/environment.json", "/packages.json"],
                    "三份文件分别取回",
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_loading_only_the_package_list_leaves_the_other_documents_out(self):
        """只取包列表：没取的另外两份不该让这次读取失败，它们那几块事实留空。"""
        server = ThreadingHTTPServer(("127.0.0.1", 0), RegistryHandler)
        server.requested = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as temporary:
                service = ModManagerService(Path(temporary))
                registry = service.load_registry(
                    f"http://127.0.0.1:{server.server_port}",
                    documents=(PACKAGES_NAME,),
                )
                self.assertEqual(len(registry.packages), REGISTRY_PACKAGE_COUNT)
                self.assertEqual(registry.game_supply, {})
                self.assertEqual(server.requested, ["/packages.json"], "只取包列表这一份")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_non_loopback_http_registry_is_rejected(self):
        with TemporaryDirectory() as temporary:
            service = ModManagerService(Path(temporary))
            with self.assertRaisesRegex(RegistryError, "HTTPS"):
                service.load_registry("http://example.com")
