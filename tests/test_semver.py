import unittest

from sprocket_mod_manager.semver import Version, satisfies, validate_range


class VersionTests(unittest.TestCase):
    def test_semver_prerelease_precedence(self):
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        self.assertEqual(sorted(map(Version.parse, reversed(ordered))), list(map(Version.parse, ordered)))

    def test_ranges(self):
        self.assertTrue(satisfies("0.1.9", ">=0.1.0 <1.0.0"))
        self.assertFalse(satisfies("1.0.0", ">=0.1.0 <1.0.0"))
        self.assertTrue(satisfies("1.9.0", "^1.2.3"))
        self.assertFalse(satisfies("2.0.0", "^1.2.3"))
        self.assertTrue(satisfies("0.2.9", "^0.2.3"))
        self.assertFalse(satisfies("0.3.0", "^0.2.3"))
        self.assertTrue(satisfies("2.4.8", "2.4.x"))
        self.assertTrue(satisfies("1.5.0", "1.0.0 - 2.0.0"))

    def test_invalid_leading_zero_is_rejected(self):
        with self.assertRaises(ValueError):
            Version.parse("01.2.3")

    def test_range_validation_checks_every_or_branch(self):
        with self.assertRaises(ValueError):
            validate_range("* || not-a-version")


if __name__ == "__main__":
    unittest.main()
