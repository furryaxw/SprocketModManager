import unittest

from sprocket_mod_manager.utilities.trust_negotiation import (
    negotiate_encoding,
    negotiate_manual_transport,
    negotiate_trust_method,
)


class TrustNegotiationTests(unittest.TestCase):
    def test_server_can_restrict_methods_without_client_downgrade(self):
        self.assertEqual(
            negotiate_trust_method(["manual", "tofu"], server_preferred="tofu"),
            "manual",
        )
        with self.assertRaisesRegex(ValueError, "no compatible"):
            negotiate_trust_method(["web_of_trust"])

    def test_client_policy_wins_over_server_preference(self):
        self.assertEqual(
            negotiate_trust_method(
                ["dnssec", "https", "manual"], server_preferred="manual",
            ),
            "dnssec",
        )

    def test_manual_transport_uses_client_priority_and_server_restrictions(self):
        self.assertEqual(
            negotiate_manual_transport(["fingerprint", "qr"], server_preferred="fingerprint"),
            "qr",
        )
        with self.assertRaisesRegex(ValueError, "manual transport"):
            negotiate_manual_transport(["qr"], client_allowed={"file"})

    def test_defaults_are_openable_without_server_extra_configuration(self):
        self.assertEqual(negotiate_trust_method(None), "https")
        self.assertEqual(
            negotiate_trust_method(["manual", "dnssec"], server_preferred="manual"),
            "dnssec",
        )
        self.assertEqual(negotiate_encoding(None), "base64url")

    def test_encoding_intersection_and_server_preference(self):
        self.assertEqual(
            negotiate_encoding(["hex", "base64"], server_preferred="hex"),
            "hex",
        )
        with self.assertRaisesRegex(ValueError, "no compatible"):
            negotiate_encoding(["multibase"], client_allowed={"hex"})


if __name__ == "__main__":
    unittest.main()
