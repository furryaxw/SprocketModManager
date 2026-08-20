from __future__ import annotations

import argparse
import hashlib
import io
import sys
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test_private_servers import DemoHandler


class RetryHandler(DemoHandler):
    download_attempts = 0

    def do_GET(self):
        if self.path == "/v1/server-info":
            self.respond({
                "protocol_version": 1,
                "server_id": "manual-retry-server",
                "name": "Manual Retry Test",
                "operator": "Local test harness",
                "demo_auth": True,
            })
            return
        if self.path.startswith("/v1/packages/private.mod/download?"):
            type(self).download_attempts += 1
            if type(self).download_attempts == 1:
                self.respond({"error": "simulated first-download failure"}, 503)
                return
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a package that fails its first download and succeeds on retry.")
    parser.add_argument("--port", type=int, default=18787)
    args = parser.parse_args()

    dll = b"manual retry test dll"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("PrivateMod.dll", dll)
    RetryHandler.package_archive = buffer.getvalue()
    RetryHandler.package_files = [{
        "name": "PrivateMod.dll",
        "target": "Mods/PrivateMod.dll",
        "sha256": hashlib.sha256(dll).hexdigest(),
    }]
    RetryHandler.package_install = {
        "scan_dlls": False,
        "exclude": [],
        "overrides": [{"match": "PrivateMod.dll", "target": "Mods"}],
    }
    server = ThreadingHTTPServer(("127.0.0.1", args.port), RetryHandler)
    print(f"Manual retry server: http://127.0.0.1:{server.server_port}", flush=True)
    print("Activation key: TEST-KEY", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
