import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_mod_manager.errors import RegistryError
from sprocket_mod_manager.service import ModManagerService


REGISTRY_BYTES = json.dumps({"schema_version": 1, "packages": []}).encode("utf-8")
REGISTRY_PACKAGE_COUNT = len(json.loads(REGISTRY_BYTES.decode("utf-8"))["packages"])


class RegistryHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/index.json":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(REGISTRY_BYTES)))
        self.end_headers()
        self.wfile.write(REGISTRY_BYTES)

    def log_message(self, _format, *_args):
        pass


class RegistrySourceTests(unittest.TestCase):
    def test_loopback_http_registry_is_allowed(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), RegistryHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as temporary:
                service = ModManagerService(Path(temporary))
                registry = service.load_registry(
                    f"http://127.0.0.1:{server.server_port}/index.json"
                )
                self.assertEqual(len(registry.packages), REGISTRY_PACKAGE_COUNT)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_non_loopback_http_registry_is_rejected(self):
        with TemporaryDirectory() as temporary:
            service = ModManagerService(Path(temporary))
            with self.assertRaisesRegex(RegistryError, "HTTPS"):
                service.load_registry("http://example.com/index.json")
