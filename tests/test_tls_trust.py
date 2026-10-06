"""信任根：系统根库 ∪ 内置公共根；缺一份也要照常干活。"""

from __future__ import annotations

import datetime
import socket
import ssl
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from sprocket_mod_manager.infrastructure import tls_trust


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def mint_chain(directory: Path) -> tuple[Path, Path, Path]:
    """自建一条链：一张 CA、一张 localhost 叶证书，外加 CA 包与叶私钥。

    返回 `(CA 包, 叶证书, 叶私钥)`；这张 CA 不在系统根库里，所以谁验得过它只可能是加载了这个包。
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    authority_key = ec.generate_private_key(ec.SECP256R1())
    authority = (
        x509.CertificateBuilder()
        .subject_name(_name("Manager Test CA"))
        .issuer_name(_name("Manager Test CA"))
        .public_key(authority_key.public_key())
        .serial_number(1)
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(authority_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(authority.subject)
        .public_key(leaf_key.public_key())
        .serial_number(2)
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(authority_key, hashes.SHA256())
    )
    bundle = directory / "roots.pem"
    bundle.write_bytes(authority.public_bytes(serialization.Encoding.PEM))
    certfile = directory / "leaf.pem"
    certfile.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    keyfile = directory / "leaf.key"
    keyfile.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return bundle, certfile, keyfile


class OneShotServer:
    """本机 TLS 服务端：递一次证书，用来问「这个客户端的信任根认不认它」。"""

    def __init__(self, certfile: Path, keyfile: Path):
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(str(certfile), str(keyfile))
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        try:
            connection, _address = self._socket.accept()
        except OSError:  # 客户端没连上：测试会在另一边失败
            return
        with connection:
            try:
                self._context.wrap_socket(connection, server_side=True)
            except (OSError, ssl.SSLError):
                pass  # 客户端拒了这张证书，服务端只负责把证书递出去

    def __enter__(self) -> "OneShotServer":
        self._thread.start()
        return self

    def __exit__(self, *_args) -> bool:
        self._socket.close()
        self._thread.join(timeout=10)
        return False


def handshake(context: ssl.SSLContext, port: int) -> None:
    """握手一次；信任根不认这张证书时抛 `ssl.SSLCertVerificationError`。"""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname="localhost"):
            pass


class TrustRootTests(unittest.TestCase):
    def setUp(self):
        tls_trust.default_ssl_context.cache_clear()
        self.addCleanup(tls_trust.default_ssl_context.cache_clear)

    def test_the_context_validates_certificates_and_hostnames(self):
        context = tls_trust.default_ssl_context()

        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_the_context_is_built_once(self):
        self.assertIs(tls_trust.default_ssl_context(), tls_trust.default_ssl_context())

    def test_the_bundle_is_added_on_top_of_the_system_store(self):
        system_only = ssl.create_default_context().cert_store_stats()["x509_ca"]

        with TemporaryDirectory() as directory:
            bundle, _certfile, _keyfile = mint_chain(Path(directory))
            with patch.object(tls_trust, "bundled_roots", return_value=bundle):
                context = tls_trust.default_ssl_context()

        self.assertEqual(context.cert_store_stats()["x509_ca"], system_only + 1)

    def test_a_root_that_only_the_bundle_has_is_enough_to_validate(self):
        with TemporaryDirectory() as directory:
            bundle, certfile, keyfile = mint_chain(Path(directory))
            with patch.object(tls_trust, "bundled_roots", return_value=bundle):
                context = tls_trust.default_ssl_context()

            with OneShotServer(certfile, keyfile) as server:
                handshake(context, server.port)

            # 对照组：只有系统根库时这张证书验不过，说明上面那次是那份包起的作用。
            with OneShotServer(certfile, keyfile) as server:
                with self.assertRaises(ssl.SSLCertVerificationError):
                    handshake(ssl.create_default_context(), server.port)

    def test_a_bundle_that_cannot_be_read_leaves_the_system_store_alone(self):
        missing = Path("no") / "such" / "bundle.pem"

        with patch.object(tls_trust, "bundled_roots", return_value=missing), self.assertLogs(
            "sprocket_mod_manager.infrastructure.tls_trust", level="WARNING"
        ) as logs:
            context = tls_trust.default_ssl_context()

        self.assertEqual(
            context.cert_store_stats()["x509_ca"],
            ssl.create_default_context().cert_store_stats()["x509_ca"],
        )
        self.assertIn("system store only", "\n".join(logs.output))

    def test_without_certifi_only_the_system_store_is_used(self):
        with patch.dict(sys.modules, {"certifi": None}):
            self.assertIsNone(tls_trust.bundled_roots())

    def test_the_bundled_roots_are_a_real_file_when_present(self):
        roots = tls_trust.bundled_roots()

        if roots is None:
            self.skipTest("certifi is not installed")
        self.assertTrue(roots.is_file())


if __name__ == "__main__":
    unittest.main()
