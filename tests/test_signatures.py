import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from sprocket_mod_manager.utilities.signatures import (
    canonical_json,
    public_key_fingerprint,
    sign_detached,
    verify_rotation_declaration,
    verify_detached,
    verify_key_status_snapshot,
)


class SignatureTests(unittest.TestCase):
    def test_canonical_json_is_stable_and_unicode_preserving(self):
        self.assertEqual(canonical_json({"b": 1, "a": "测试"}), '{"a":"测试","b":1}'.encode())
        with self.assertRaises(ValueError):
            canonical_json({"value": float("nan")})

    def test_ed25519_detached_signature_round_trip(self):
        private = Ed25519PrivateKey.generate()
        public = private.public_key()
        manifest = {"version": "1.0.0", "files": [{"name": "Mod.dll", "sha256": "a" * 64}]}
        envelope = sign_detached(manifest, private, key_id="server-key-1")
        verify_detached(manifest, envelope, public)
        self.assertTrue(public_key_fingerprint(public).startswith("sha256:"))
        with self.assertRaisesRegex(ValueError, "verification failed"):
            verify_detached({**manifest, "version": "2.0.0"}, envelope, public)

    def test_invalid_envelope_is_rejected(self):
        private = Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(ValueError, "unsupported"):
            verify_detached({}, {"algorithm": "pgp"}, private.public_key())

    def test_key_status_snapshot_rejects_tampering_and_expiry(self):
        private = Ed25519PrivateKey.generate()
        public = private.public_key()
        raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        import base64
        identity = {
            "algorithm": "ed25519", "encoding": "base64url", "key_id": "key-1",
            "public_key": base64.urlsafe_b64encode(raw).decode().rstrip("="),
            "fingerprint": public_key_fingerprint(public),
        }
        status = {
            "schema_version": 1, "server_id": "server", "active_key_id": "key-1",
            "revoked_key_ids": [], "issued_at": 1000, "expires_at": 1100,
        }
        snapshot = {"status": status, "signature": sign_detached(status, private, key_id="key-1")}
        self.assertEqual(
            verify_key_status_snapshot(snapshot, identity, server_id="server", now=1050)["active_key_id"],
            "key-1",
        )
        with self.assertRaisesRegex(ValueError, "expired"):
            verify_key_status_snapshot(snapshot, identity, server_id="server", now=1100)
        with self.assertRaisesRegex(ValueError, "verification failed"):
            verify_key_status_snapshot(
                {"status": {**status, "server_id": "other"}, "signature": snapshot["signature"]},
                identity, server_id="server", now=1050,
            )

    def test_key_rotation_requires_old_and_new_signatures(self):
        import base64
        from cryptography.hazmat.primitives import serialization
        previous = Ed25519PrivateKey.generate()
        next_key = Ed25519PrivateKey.generate()
        raw = next_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        declaration = {
            "schema_version": 1, "server_id": "server", "previous_key_id": "key-1",
            "next_key_id": "key-2", "next_public_key": base64.urlsafe_b64encode(raw).decode().rstrip("="),
            "effective_at": 2000, "previous_key_expires_at": 5600,
        }
        verified = verify_rotation_declaration(
            declaration, sign_detached(declaration, previous, key_id="key-1"),
            sign_detached(declaration, next_key, key_id="key-2"), previous.public_key(), now=1000,
        )
        self.assertEqual(public_key_fingerprint(verified), public_key_fingerprint(next_key.public_key()))
        with self.assertRaisesRegex(ValueError, "signature is invalid"):
            verify_rotation_declaration(
                {**declaration, "next_key_id": "attacker"},
                sign_detached(declaration, previous, key_id="key-1"),
                sign_detached(declaration, next_key, key_id="key-2"), previous.public_key(), now=1000,
            )


if __name__ == "__main__":
    unittest.main()
