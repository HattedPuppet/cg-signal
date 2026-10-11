import json
from datetime import datetime
from pathlib import Path
import unittest

from cg_signal.article_policy import article_is_publishable


class ArticlePolicyTests(unittest.TestCase):
    def test_shared_publication_policy_fixtures(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "publication_policy.json").read_text(encoding="utf-8")
        )
        now = datetime.fromisoformat(fixture["now"])
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(article_is_publishable(case["article"], now=now), case["expected"])


if __name__ == "__main__":
    unittest.main()
