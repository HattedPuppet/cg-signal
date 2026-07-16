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
            ["Technical art & optimization", "Pipeline, tools & automation"],
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

    def test_industry_story_can_have_multiple_topics(self):
        self.assertEqual(
            classify_topics(
                "Animation studio announces acquisition and new funding",
                "The founder discussed the deal",
                "Industry & Business",
            ),
            ["Studios & people", "Business, funding & acquisitions"],
        )

    def test_japanese_rights_story_is_legal(self):
        self.assertEqual(
            classify_topics(
                "ゲーム会社が著作権侵害をめぐり訴訟",
                "権利保護について発表",
                "Industry & Business",
            ),
            ["Legal & policy"],
        )

    def test_unmatched_topics_use_lane_specific_fallbacks(self):
        self.assertEqual(
            classify_topics("A general creative story", "No specific technique", "Tech & Development"),
            ["Other production"],
        )
        self.assertEqual(
            classify_topics("A general creative story", "No specific theme", "Industry & Business"),
            ["Other industry"],
        )


if __name__ == "__main__":
    unittest.main()
