"""`match` 的口径：`*` 只吃一层，`**` 吃子树，没有按文件名的兜底。"""

import unittest

from sprocket_mod_manager.utilities.matching import fixed_directory, matches


class MatchTests(unittest.TestCase):
    def test_a_star_stays_in_one_segment(self):
        self.assertTrue(matches("BepInEx/plugins/*.dll", "BepInEx/plugins/X.dll"))
        self.assertFalse(matches("BepInEx/plugins/*.dll", "BepInEx/plugins/Mod/X.dll"))

    def test_a_pattern_without_a_separator_does_not_reach_into_subdirectories(self):
        self.assertTrue(matches("*.dll", "X.dll"))
        self.assertFalse(matches("*.dll", "BepInEx/plugins/X.dll"))

    def test_double_star_walks_the_tree_and_may_be_empty(self):
        self.assertTrue(matches("**", "BepInEx/plugins/Mod/X.dll"))
        self.assertTrue(matches("**/*.dll", "X.dll"))
        self.assertTrue(matches("**/*.dll", "a/b/X.dll"))
        self.assertTrue(matches("BepInEx/plugins/**", "BepInEx/plugins/a/b.png"))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(matches("bepinex/plugins/*.DLL", "BepInEx/plugins/x.dll"))

    def test_a_question_mark_is_one_character_of_the_segment(self):
        self.assertTrue(matches("Mods/X?.dll", "Mods/X1.dll"))
        self.assertFalse(matches("Mods/X?.dll", "Mods/X12.dll"))

    def test_a_character_class_is_glob(self):
        self.assertTrue(matches("Mods/[ab]*.dll", "Mods/alpha.dll"))
        self.assertFalse(matches("Mods/[ab]*.dll", "Mods/cloud.dll"))

    def test_the_fixed_directory_is_the_anchor(self):
        self.assertEqual(
            fixed_directory("BepInEx/plugins/Mod/**"), ("BepInEx", "plugins", "Mod")
        )
        self.assertEqual(fixed_directory("BepInEx/plugins/*.dll"), ("BepInEx", "plugins"))
        self.assertEqual(fixed_directory("**"), ())
        self.assertEqual(fixed_directory("*.dll"), ())


if __name__ == "__main__":
    unittest.main()
