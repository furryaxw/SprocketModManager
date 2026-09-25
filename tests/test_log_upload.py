import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.infrastructure.log_upload import upload_log_file


class _Response:
    status = 201
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, _size=-1): return b"https://paste.furryaxw.top/@/anonymous/example"


class LogUploadTests(unittest.TestCase):
    def test_upload_posts_plain_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Latest.log"
            path.write_text("token=secret", encoding="utf-8")
            with patch("sprocket_mod_manager.infrastructure.log_upload.urlopen", return_value=_Response()) as opener:
                result = upload_log_file(path, "https://logs.example/upload", app_version="1.0.0")
            request = opener.call_args.args[0]
            self.assertEqual(result.status, 201)
            self.assertEqual(request.headers["Content-type"], "text/plain; charset=utf-8")
            self.assertEqual(request.data, b"token=secret")
            self.assertTrue(result.url.startswith("https://paste.furryaxw.top/"))

    def test_upload_sends_the_tail_within_the_byte_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Latest.log"
            path.write_text("0123456789", encoding="utf-8")
            with patch("sprocket_mod_manager.infrastructure.log_upload.urlopen", return_value=_Response()) as opener:
                result = upload_log_file(path, "https://logs.example/upload", app_version="1.0.0", max_bytes=4)
            self.assertEqual(opener.call_args.args[0].data, b"6789")
            self.assertEqual(result.bytes_uploaded, 4)

    def test_a_missing_log_file_is_rejected_before_uploading(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                upload_log_file(
                    Path(directory) / "Latest.log", "https://logs.example/upload", app_version="1"
                )

    def test_endpoint_must_be_https(self):
        with self.assertRaises(Exception):
            upload_log_file(Path("."), "http://localhost/upload", app_version="1")


if __name__ == "__main__":
    unittest.main()
