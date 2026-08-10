import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.log_upload import latest_log_path, upload_latest_log


class _Response:
    status = 201
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, _size=-1): return b"https://paste.furryaxw.top/@/anonymous/example"


class LogUploadTests(unittest.TestCase):
    def test_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = latest_log_path(Path(directory)); path.parent.mkdir(); path.write_text("token=secret\nready", encoding="utf-8")
            self.assertEqual(path.name, "Latest.log")
            self.assertEqual(path.read_text(encoding="utf-8"), "token=secret\nready")

    def test_upload_posts_plain_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = latest_log_path(Path(directory)); path.parent.mkdir(); path.write_text("token=secret", encoding="utf-8")
            with patch("sprocket_mod_manager.log_upload.urlopen", return_value=_Response()) as opener:
                result = upload_latest_log(Path(directory), "https://logs.example/upload", app_version="1.0.0")
            request = opener.call_args.args[0]
            self.assertEqual(result.status, 201)
            self.assertEqual(request.headers["Content-type"], "text/plain; charset=utf-8")
            self.assertEqual(request.data, b"token=secret")
            self.assertTrue(result.url.startswith("https://paste.furryaxw.top/"))

    def test_endpoint_must_be_https(self):
        with self.assertRaises(Exception):
            upload_latest_log(Path("."), "http://localhost/upload", app_version="1")
