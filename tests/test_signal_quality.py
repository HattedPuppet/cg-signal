import json
import unittest
from pathlib import Path

from cg_signal.classification import apply_article_classification
from cg_signal.config import FEEDS
from cg_signal.dedupe import same_story


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "signal_quality.json"
CONFIGURED_SOURCE_IDS = {source["id"] for source in FEEDS}
BODY_LIKE_KEYS = {
    "abstract",
    "article_body",
    "body",
    "content",
    "description",
    "excerpt",
    "html",
    "summary",
    "text",
}


class SignalQualityCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_schema_and_provenance(self):
        fixture = self.fixture
        self.assertEqual(fixture.get("schema_version"), 1)
        self.assertEqual(
            set(fixture), {"schema_version", "captured_on", "content_policy", "classification_cases", "dedupe_pairs"}
        )
        self.assertEqual(len(fixture["classification_cases"]), 20)
        self.assertEqual(len(fixture["dedupe_pairs"]), 12)

        ids = []
        for case in fixture["classification_cases"]:
            self.assertEqual(set(case), {"id", "title", "source_id", "expected"})
            self.assertTrue(case["id"].strip())
            self.assertTrue(case["title"].strip())
            self.assertIn(case["source_id"], CONFIGURED_SOURCE_IDS)
            self.assertEqual(set(case["expected"]), {"lane", "software_tags", "topic_tags"})
            self.assertIsInstance(case["expected"]["software_tags"], list)
            self.assertIsInstance(case["expected"]["topic_tags"], list)
            ids.append(case["id"])

        for pair in fixture["dedupe_pairs"]:
            self.assertEqual(set(pair), {"id", "left", "right", "expected_same_story"})
            self.assertTrue(pair["id"].strip())
            self.assertIsInstance(pair["expected_same_story"], bool)
            ids.append(pair["id"])
            for side in ("left", "right"):
                self.assertEqual(set(pair[side]), {"title", "source_id"})
                self.assertTrue(pair[side]["title"].strip())
                self.assertIn(pair[side]["source_id"], CONFIGURED_SOURCE_IDS)

        self.assertEqual(len(ids), len(set(ids)), "fixture IDs must be unique")

        def inspect(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    self.assertNotIn(key.lower(), BODY_LIKE_KEYS, f"body-like fixture key: {key}")
                    inspect(nested)
            elif isinstance(value, str):
                self.assertNotIn("http://", value.lower(), "fixture must not contain real URLs")
                self.assertNotIn("https://", value.lower(), "fixture must not contain real URLs")

        inspect(fixture)

    def test_classification_quality_gates(self):
        cases = self.fixture["classification_cases"]
        metrics = {"lane": 18, "software": 20, "topic": 18}
        achieved = {name: 0 for name in metrics}
        mismatches = {name: [] for name in metrics}

        for case in cases:
            classified = apply_article_classification(
                {
                    "title": case["title"],
                    "summary": "",
                    "source_id": case["source_id"],
                }
            )
            expected = case["expected"]
            actual = {
                "lane": classified["lane"],
                "software_tags": classified["software_tags"],
                "topic_tags": classified["topic_tags"],
            }
            for name, expected_key in (
                ("lane", "lane"),
                ("software", "software_tags"),
                ("topic", "topic_tags"),
            ):
                if actual[expected_key] == expected[expected_key]:
                    achieved[name] += 1
                else:
                    mismatches[name].append(
                        f"{case['id']} expected={expected[expected_key]!r} actual={actual[expected_key]!r} "
                        f"title={case['title'][:80]!r}"
                    )

        failures = []
        for name, threshold in metrics.items():
            if achieved[name] < threshold:
                evidence = "; ".join(mismatches[name]) or "none"
                failures.append(
                    f"metric={name} threshold={threshold}/{len(cases)} achieved={achieved[name]}/{len(cases)} "
                    f"mismatches=[{evidence}]"
                )
        if failures:
            self.fail("signal-quality classification gates failed: " + " | ".join(failures))

    def test_dedupe_quality_gates(self):
        pairs = self.fixture["dedupe_pairs"]
        fixed_time = "2026-08-11T12:00:00+00:00"
        positive_total = sum(1 for pair in pairs if pair["expected_same_story"])
        negative_total = sum(1 for pair in pairs if not pair["expected_same_story"])
        positive_achieved = 0
        negative_achieved = 0
        positive_mismatches = []
        negative_mismatches = []

        for pair in pairs:
            left = {
                "title": pair["left"]["title"],
                "source_id": pair["left"]["source_id"],
                "url": f"https://left.fixture.invalid/{pair['id']}",
                "published_at": fixed_time,
                "_refs": [],
            }
            right = {
                "title": pair["right"]["title"],
                "source_id": pair["right"]["source_id"],
                "url": f"https://right.fixture.invalid/{pair['id']}",
                "published_at": "2026-08-11T12:30:00+00:00",
                "_refs": [],
            }
            actual = same_story(left, right)
            expected = pair["expected_same_story"]
            evidence = (
                f"{pair['id']} expected={expected!r} actual={actual!r} "
                f"title={pair['left']['title'][:60]!r} / {pair['right']['title'][:60]!r}"
            )
            if expected:
                if actual:
                    positive_achieved += 1
                else:
                    positive_mismatches.append(evidence)
            elif not actual:
                negative_achieved += 1
            else:
                negative_mismatches.append(evidence)

        failures = []
        if positive_achieved < 3:
            failures.append(
                f"metric=dedupe-positive threshold=3/{positive_total} achieved={positive_achieved}/{positive_total} "
                f"mismatches=[{'; '.join(positive_mismatches) or 'none'}]"
            )
        if negative_achieved < 4:
            failures.append(
                f"metric=dedupe-negative-specificity threshold=4/{negative_total} achieved={negative_achieved}/{negative_total} "
                f"mismatches=[{'; '.join(negative_mismatches) or 'none'}]"
            )
        if failures:
            self.fail("signal-quality dedupe gates failed: " + " | ".join(failures))


if __name__ == "__main__":
    unittest.main()
