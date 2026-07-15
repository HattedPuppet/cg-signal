import unittest

from server import classify_lane


class LaneClassificationTests(unittest.TestCase):
    def test_official_unreal_release_is_technical(self):
        self.assertEqual(
            classify_lane("Unreal Engine 5.8 is now available", "Rendering and workflow features", "unreal-engine"),
            "Tech & Development",
        )

    def test_substance_workflow_is_technical(self):
        self.assertEqual(
            classify_lane("Substance Designer workflow breakdown", "A procedural material technique tutorial", "80-level"),
            "Tech & Development",
        )

    def test_layoff_report_is_industry(self):
        self.assertEqual(
            classify_lane("Animation studio announces layoffs", "The company is restructuring its business", "cartoon-brew"),
            "Industry & Business",
        )

    def test_explicit_jobs_story_overrides_technical_source_tie(self):
        self.assertEqual(
            classify_lane("Top job picks for artists", "New studio openings and careers", "80-level"),
            "Industry & Business",
        )

    def test_japanese_earnings_report_is_industry(self):
        self.assertEqual(
            classify_lane("ゲーム会社が決算を発表", "売上と利益、市場の見通しを説明", "gamebusiness"),
            "Industry & Business",
        )

    def test_strong_technical_story_overrides_business_source_prior(self):
        self.assertEqual(
            classify_lane("Houdini VFX workflow breakdown", "Rendering tutorial and pipeline technique", "gamebusiness"),
            "Tech & Development",
        )


if __name__ == "__main__":
    unittest.main()
