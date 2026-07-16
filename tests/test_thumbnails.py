import unittest
import xml.etree.ElementTree as ET

from server import extract_page_image, first_image, preferred_child_text


class ThumbnailTests(unittest.TestCase):
    def test_open_graph_image_is_found_in_any_attribute_order(self):
        markup = "<meta content='/images/preview.webp' property='og:image'>"
        self.assertEqual(
            extract_page_image(markup, "https://example.com/articles/one"),
            "https://example.com/images/preview.webp",
        )

    def test_twitter_image_is_a_fallback(self):
        markup = '<meta name="twitter:image" content="https://cdn.example.com/card.jpg">'
        self.assertEqual(
            extract_page_image(markup, "https://example.com/"),
            "https://cdn.example.com/card.jpg",
        )

    def test_content_encoded_is_preferred_over_short_description(self):
        item = ET.fromstring(
            """
            <item xmlns:content="https://purl.org/rss/1.0/modules/content/">
              <description>Short text only</description>
              <content:encoded>&lt;p&gt;Article&lt;/p&gt;&lt;img src="https://example.com/lead.jpg"&gt;</content:encoded>
            </item>
            """
        )
        rich = preferred_child_text(item, ("encoded", "content", "description"))
        self.assertIn("lead.jpg", rich)
        self.assertEqual(first_image(item, rich), "https://example.com/lead.jpg")


if __name__ == "__main__":
    unittest.main()
