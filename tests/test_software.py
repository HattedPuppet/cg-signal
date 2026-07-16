import unittest

from server import classify_software


class SoftwareClassificationTests(unittest.TestCase):
    def test_title_order_selects_primary_software(self):
        self.assertEqual(
            classify_software("Blender to Unreal Engine workflow", "Export and rendering notes"),
            ["Blender", "Unreal Engine"],
        )

    def test_title_match_precedes_summary_match(self):
        self.assertEqual(
            classify_software("A Houdini simulation breakdown", "The assets are later imported into Unreal Engine"),
            ["Houdini", "Unreal Engine"],
        )

    def test_specific_substance_product_suppresses_generic_group(self):
        self.assertEqual(
            classify_software("Substance Designer procedural materials", "A Substance 3D workflow"),
            ["Substance Designer"],
        )

    def test_generic_substance_story_has_a_shared_group(self):
        self.assertEqual(
            classify_software("Substance 3D receives an update", "New Adobe tools"),
            ["Substance 3D"],
        )

    def test_unmatched_story_has_no_software_tag(self):
        self.assertEqual(classify_software("Lighting theory", "A general production article"), [])


if __name__ == "__main__":
    unittest.main()
