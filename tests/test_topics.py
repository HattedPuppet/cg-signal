import unittest

from server import classify_topics


class TopicClassificationTests(unittest.TestCase):
    def test_production_story_can_have_multiple_topics(self):
        self.assertEqual(
            classify_topics(
                "Optimizing shader performance in a Blender pipeline",
                "A workflow breakdown",
                "Tech & Development",
            ),
            [
                "Technical art & optimization",
                "Pipeline, tools & automation",
                "Breakdowns & production stories",
            ],
        )

    def test_japanese_animation_story_is_classified(self):
        self.assertEqual(
            classify_topics(
                "フェイシャルアニメーションのモーションキャプチャ制作",
                "現場の工程を解説",
                "Tech & Development",
            ),
            ["Animation, rigging & mocap"],
        )

    def test_industry_stories_have_no_production_subcategories(self):
        self.assertEqual(
            classify_topics(
                "Animation studio announces acquisition and new funding",
                "The founder discussed the deal",
                "Industry & Business",
            ),
            [],
        )

    def test_general_production_topics_are_more_specific(self):
        cases = [
            ("Creature production breakdown", "A detailed making of", "Breakdowns & production stories"),
            ("SIGGRAPH neural rendering research", "A new paper", "Research & emerging tech"),
            ("A new tool version is released", "The update adds features", "Releases & product updates"),
            ("Environment art showcase", "A gallery for inspiration", "Assets & inspiration"),
        ]
        for title, summary, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, classify_topics(title, summary, "Tech & Development"))
        self.assertEqual(
            classify_topics(
                "ゲーム会社が著作権侵害をめぐり訴訟",
                "権利保護について発表",
                "Industry & Business",
            ),
            [],
        )

    def test_unmatched_production_story_uses_fallback(self):
        self.assertEqual(
            classify_topics("A general creative story", "No specific technique", "Tech & Development"),
            ["Other production"],
        )


if __name__ == "__main__":
    unittest.main()
