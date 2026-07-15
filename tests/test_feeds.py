import unittest
import xml.etree.ElementTree as ET

from server import parse_feed_document


class FeedParsingTests(unittest.TestCase):
    def test_valid_feed_is_parsed(self):
        root = parse_feed_document(b"<?xml version='1.0'?><rss><channel /></rss>")
        self.assertEqual(root.tag, "rss")

    def test_script_appended_after_atom_feed_is_ignored(self):
        payload = (
            b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>"
            b"<entry><title>Unreal update</title></entry></feed>"
            b"<script>cloudflare()</script>"
        )
        root = parse_feed_document(payload)
        entries = [node for node in root.iter() if node.tag.endswith("entry")]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].find("{http://www.w3.org/2005/Atom}title").text, "Unreal update")

    def test_genuinely_broken_xml_still_raises(self):
        with self.assertRaises(ET.ParseError):
            parse_feed_document(b"<rss><channel>")


if __name__ == "__main__":
    unittest.main()
