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

    def test_substance_products_share_one_category(self):
        self.assertEqual(
            classify_software("Substance Designer procedural materials", "A Substance 3D workflow"),
            ["Substance 3D"],
        )
        self.assertEqual(
            classify_software("Substance Painter texturing workflow", "An Adobe tool tutorial"),
            ["Substance 3D"],
        )

    def test_generic_substance_story_has_a_shared_group(self):
        self.assertEqual(
            classify_software("Substance 3D receives an update", "New Adobe tools"),
            ["Substance 3D"],
        )

    def test_unity_and_ai_are_watch_categories(self):
        self.assertEqual(
            classify_software("Unity 6 lighting workflow", "A game engine tutorial"),
            ["Unity"],
        )
        self.assertEqual(
            classify_software("Generative AI for concept art", "A diffusion model workflow"),
            ["AI"],
        )

    def test_community_does_not_create_a_unity_match(self):
        self.assertEqual(
            classify_software("Community art showcase", "Artists share their work"),
            [],
        )

    def test_spine_is_not_a_top_level_software_category(self):
        self.assertEqual(
            classify_software("Spine 2D animation workflow", "A character rigging tutorial"),
            [],
        )

    def test_unmatched_story_has_no_software_tag(self):
        self.assertEqual(classify_software("Lighting theory", "A general production article"), [])


if __name__ == "__main__":
    unittest.main()
