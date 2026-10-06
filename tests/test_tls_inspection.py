"""证书被拒时的对端记录：只握手、只解码，不改变校验结果，也不发 HTTP 请求。"""

from __future__ import annotations

import datetime
import ssl
import unittest
from unittest.mock import patch
from urllib.error import URLError

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from sprocket_mod_manager.infrastructure import tls_inspection


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def interception_certificate() -> tuple[bytes, str]:
    """一张「中间人」证书：主题是站点名，签发者是别的东西。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    authority_key = ec.generate_private_key(ec.SECP256R1())
    authority = (
        x509.CertificateBuilder()
        .subject_name(_name("Fake Interception CA"))
        .issuer_name(_name("Fake Interception CA"))
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
        .subject_name(_name("api.github.com"))
        .issuer_name(authority.subject)
        .public_key(leaf_key.public_key())
        .serial_number(2)
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(authority_key, hashes.SHA256())
    )
    return leaf.public_bytes(serialization.Encoding.DER), leaf.not_valid_after_utc.date().isoformat()


class FakeSocket:
    def __init__(self, der: bytes):
        self.der = der
        self.closed = False

    def __enter__(self) -> "FakeSocket":
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def close(self) -> None:
        self.closed = True

    def getpeercert(self, binary_form: bool = False):
        return self.der if binary_form else {}


class PeerCertificateTests(unittest.TestCase):
    def test_a_certificate_is_described_by_subject_and_issuer(self) -> None:
        der, not_after = interception_certificate()

        described = tls_inspection.describe_der_certificate(der)

        self.assertIsNotNone(described)
        self.assertEqual(described.subject, "CN=api.github.com")
        self.assertEqual(described.issuer, "CN=Fake Interception CA")
        self.assertEqual(described.not_after, not_after)

    def test_something_that_is_not_a_certificate_is_not_described(self) -> None:
        self.assertIsNone(tls_inspection.describe_der_certificate(b"not a certificate"))

    def test_the_probe_handshakes_without_validating_anything(self) -> None:
        der, _ = interception_certificate()
        contexts: list[ssl.SSLContext] = []
        server_names: list[str | None] = []

        def wrap(context, _raw, server_hostname=None):
            contexts.append(context)
            server_names.append(server_hostname)
            return FakeSocket(der)

        with patch("socket.create_connection", return_value=FakeSocket(der)), patch.object(
            ssl.SSLContext, "wrap_socket", wrap
        ):
            note = tls_inspection.peer_certificate_note("https://api.github.com/repos/example/mod")

        self.assertIn("issuer=CN=Fake Interception CA", note)
        self.assertIn("subject=CN=api.github.com", note)
        self.assertIn("not_after=", note)
        self.assertEqual(server_names, ["api.github.com"])
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].verify_mode, ssl.CERT_NONE)
        self.assertFalse(contexts[0].check_hostname)

    def test_a_handshake_that_cannot_be_read_says_so(self) -> None:
        with patch("socket.create_connection", side_effect=OSError("no route to host")):
            note = tls_inspection.peer_certificate_note("https://api.github.com/")

        self.assertEqual(note, "unavailable")

    def test_a_probe_that_blows_up_never_masks_the_real_failure(self) -> None:
        """调用方正在上报一个更要紧的失败：补充信息只准退化成「读不出来」。"""
        with patch(
            "sprocket_mod_manager.infrastructure.tls_inspection.peer_certificate",
            side_effect=RuntimeError("probe bug"),
        ):
            note = tls_inspection.peer_certificate_note("https://api.github.com/")

        self.assertEqual(note, "unavailable")

    def test_only_https_gets_probed(self) -> None:
        with patch("sprocket_mod_manager.infrastructure.tls_inspection.peer_certificate") as probe:
            note = tls_inspection.peer_certificate_note("http://api.github.com/")

        self.assertEqual(note, "unavailable")
        probe.assert_not_called()

    def test_a_broken_port_is_not_probed(self) -> None:
        with patch("sprocket_mod_manager.infrastructure.tls_inspection.peer_certificate") as probe:
            note = tls_inspection.peer_certificate_note("https://api.github.com:not-a-port/")

        self.assertEqual(note, "unavailable")
        probe.assert_not_called()

    def test_the_note_carries_the_port_the_url_asked_for(self) -> None:
        certificate = tls_inspection.PeerCertificate("CN=host", "CN=ca", "2026-11-27")
        with patch(
            "sprocket_mod_manager.infrastructure.tls_inspection.peer_certificate",
            return_value=certificate,
        ) as probe:
            tls_inspection.peer_certificate_note("https://api.github.com:8443/x")

        self.assertEqual(probe.call_args.args, ("api.github.com", 8443))


class CertificateErrorTests(unittest.TestCase):
    @staticmethod
    def _rejected_certificate() -> ssl.SSLCertVerificationError:
        return ssl.SSLCertVerificationError(
            1, "certificate verify failed: unable to get local issuer certificate"
        )

    def test_the_error_urllib_wrapped_is_found(self) -> None:
        rejected = self._rejected_certificate()

        self.assertIs(
            tls_inspection.certificate_verification_error(URLError(rejected)),
            rejected,
        )

    def test_an_error_hidden_behind_a_cause_is_found(self) -> None:
        rejected = self._rejected_certificate()
        wrapped = URLError(ValueError("proxy refused"))
        wrapped.__cause__ = rejected

        self.assertIs(tls_inspection.certificate_verification_error(wrapped), rejected)

    def test_a_plain_network_failure_is_not_a_certificate_error(self) -> None:
        self.assertIsNone(
            tls_inspection.certificate_verification_error(TimeoutError("The read operation timed out"))
        )

    def test_a_cycle_in_the_chain_does_not_hang(self) -> None:
        first = URLError("outer")
        second = URLError("inner")
        first.reason = second
        second.reason = first

        self.assertIsNone(tls_inspection.certificate_verification_error(first))


if __name__ == "__main__":
    unittest.main()
