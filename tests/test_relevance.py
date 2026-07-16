import unittest

from server import score_relevance


class RelevanceScoringTests(unittest.TestCase):
    def test_unreal_workflow_is_a_focus_story(self):
        score, reasons = score_relevance(
            "Unreal Engine rendering workflow breakdown",
            "A practical shader and performance tutorial",
            "80-level",
            "Tech & Development",
        )
        self.assertGreaterEqual(score, 60)
        self.assertIn("Unreal Engine", reasons)

    def test_promotional_story_is_deprioritized(self):
        technical_score, _ = score_relevance(
            "Blender geometry nodes tutorial",
            "A procedural workflow breakdown",
            "80-level",
            "Tech & Development",
        )
        promotional_score, _ = score_relevance(
            "Blender asset bundle sale",
            "Limited-time discount",
            "80-level",
            "Tech & Development",
        )
        self.assertGreater(technical_score, promotional_score)

    def test_multiple_sources_add_confidence(self):
        single_score, _ = score_relevance(
            "Rendering technology update", "New feature details", "cgworld", "Tech & Development", 1
        )
        multi_score, reasons = score_relevance(
            "Rendering technology update", "New feature details", "cgworld", "Tech & Development", 3
        )
        self.assertGreater(multi_score, single_score)
        self.assertIn("Multiple sources", reasons)

    def test_unity_and_ai_matches_are_explained(self):
        _score, reasons = score_relevance(
            "Unity editor adds generative AI tools",
            "A machine learning workflow update",
            "80-level",
            "Tech & Development",
        )
        self.assertIn("Unity", reasons)
        self.assertIn("AI", reasons)

    def test_substance_products_use_the_shared_relevance_reason(self):
        _score, reasons = score_relevance(
            "Substance Painter and Designer workflow",
            "An Adobe Substance 3D tutorial",
            "80-level",
            "Tech & Development",
        )
        self.assertIn("Substance 3D", reasons)
        self.assertNotIn("Substance Painter", reasons)
        self.assertNotIn("Substance Designer", reasons)


if __name__ == "__main__":
    unittest.main()
