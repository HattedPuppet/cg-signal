import unittest
from datetime import datetime, timezone

from server import canonical_url, same_story


def article(title: str, url: str, refs=None):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "title": title,
        "url": url,
        "published_at": now,
        "_refs": refs or [],
    }


class DeduplicationTests(unittest.TestCase):
    def test_tracking_parameters_do_not_make_a_new_story(self):
        left = canonical_url("https://example.com/news?utm_source=mail&id=4")
        right = canonical_url("https://example.com/news?id=4")
        self.assertEqual(left, right)

    def test_nearly_identical_titles_are_grouped(self):
        left = article("Blender 5.2 LTS Has Been Released", "https://a.example/blender")
        right = article("Blender 5.2 LTS has officially been released!", "https://b.example/blender")
        self.assertTrue(same_story(left, right))

    def test_japanese_and_english_product_release_is_grouped(self):
        left = article("Blender 5.2 LTS Has Been Released", "https://a.example/release")
        right = article("Blender 5.2 LTSが正式リリース！新機能を紹介", "https://b.example/release")
        self.assertTrue(same_story(left, right))

    def test_product_tutorial_is_not_folded_into_release_news(self):
        left = article("Blender 5.2 LTS Has Been Released", "https://a.example/release")
        right = article("Blender 5.2 Geometry Nodes Tutorial", "https://b.example/tutorial")
        self.assertFalse(same_story(left, right))

    def test_shared_official_link_is_grouped(self):
        official = "https://vendor.example/product/announcement"
        left = article("A major update is here", "https://a.example/story", [official])
        right = article("大型アップデートが公開", "https://b.example/story", [official])
        self.assertTrue(same_story(left, right))


if __name__ == "__main__":
    unittest.main()
