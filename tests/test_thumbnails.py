import os
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import xml.etree.ElementTree as ET
from email.message import Message
from unittest import mock

from cg_signal.feeds import extract_page_image, first_image, preferred_child_text
from cg_signal.thumbnails import (
    MAX_THUMBNAIL_BYTES,
    canonical_thumbnail_reference,
    prune_thumbnail_store,
    read_verified_thumbnail,
    store_thumbnail,
    validate_thumbnail_bytes,
    validate_thumbnail_root,
)


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

    def test_supported_bytes_require_exact_mime_and_magic(self):
        fixtures = (
            ("image/jpeg", b"\xff\xd8\xffpayload", ".jpg"),
            ("image/png", b"\x89PNG\r\n\x1a\npayload", ".png"),
            ("image/webp", b"RIFF1234WEBPpayload", ".webp"),
        )
        for mime, body, suffix in fixtures:
            with self.subTest(mime=mime):
                validated = validate_thumbnail_bytes(body, mime)
                self.assertIsNotNone(validated)
                self.assertTrue(validated.reference.endswith(suffix))
                self.assertIsNone(validate_thumbnail_bytes(body, "text/html"))
                self.assertIsNone(validate_thumbnail_bytes(body, "image/jpeg; charset=utf-8"))
        for mime, body in (
            ("image/jpeg", b"<html>"),
            ("image/png", b"GIF89a"),
            ("image/webp", b"RIFF1234AVIF"),
            ("image/png", b""),
            ("image/jpeg", b"\xff\xd8\xff" + b"x" * MAX_THUMBNAIL_BYTES),
        ):
            self.assertIsNone(validate_thumbnail_bytes(body, mime))

    def test_response_media_type_parameters_are_not_treated_as_exact_mime(self):
        headers = Message()
        headers["Content-Type"] = "image/jpeg; charset=binary"
        response = type("Response", (), {"status": 200, "headers": headers, "body": b"\xff\xd8\xffx"})()
        from cg_signal.thumbnails import validate_thumbnail_response
        self.assertIsNone(validate_thumbnail_response(response))

    def test_store_is_content_addressed_and_rejects_bad_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = b"\x89PNG\r\n\x1a\nasset"
            validated = validate_thumbnail_bytes(body, "image/png")
            reference = store_thumbnail(root, validated)
            self.assertEqual(reference, canonical_thumbnail_reference(reference))
            self.assertEqual(read_verified_thumbnail(root, reference).body, body)
            self.assertIsNone(read_verified_thumbnail(root, "thumbnails/../escape.png"))
            target = root / Path(reference).name
            target.write_bytes(b"corrupt")
            self.assertIsNone(read_verified_thumbnail(root, reference))

    def test_symlinked_store_root_is_rejected_without_touching_external_sentinel(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            anchor = base / "cache"
            anchor.mkdir()
            external = base / "external"
            external.mkdir()
            sentinel = external / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            root = anchor / "thumbnails"
            try:
                root.symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable")
            with self.assertRaises(ValueError):
                validate_thumbnail_root(root, anchor)
            body = b"\x89PNG\r\n\x1a\nasset"
            validated = validate_thumbnail_bytes(body, "image/png")
            with self.assertRaises(ValueError):
                store_thumbnail(root, validated, expected_anchor=anchor)
            with self.assertRaises(ValueError):
                prune_thumbnail_store(root, {"entries": {}}, expected_anchor=anchor)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertEqual(list(external.iterdir()), [sentinel])

    def test_store_quota_is_hard_under_concurrent_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "cache" / "thumbnails"
            anchor = root.parent
            root.mkdir(parents=True)
            bodies = [b"\x89PNG\r\n\x1a\n" + bytes([index]) * 8 for index in range(8)]

            def publish(body):
                try:
                    return store_thumbnail(
                        root,
                        body,
                        "image/png",
                        expected_anchor=anchor,
                        max_files=2,
                        max_bytes=128,
                    )
                except ValueError:
                    return ""

            with ThreadPoolExecutor(max_workers=len(bodies)) as executor:
                results = list(executor.map(publish, bodies))
            self.assertEqual(sum(bool(result) for result in results), len(bodies))
            self.assertEqual(len(list(root.glob("*.png"))), 2)
            self.assertLessEqual(sum(path.stat().st_size for path in root.glob("*.png")), 128)

    def test_store_evicts_oldest_asset_at_file_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_reference = store_thumbnail(
                root, b"\x89PNG\r\n\x1a\nold", "image/png", max_files=1, max_bytes=128
            )
            old_path = root / Path(old_reference).name
            os.utime(old_path, (1, 1))

            new_reference = store_thumbnail(
                root, b"\x89PNG\r\n\x1a\nnew", "image/png", max_files=1, max_bytes=128
            )

            self.assertFalse(old_path.exists())
            self.assertTrue((root / Path(new_reference).name).is_file())
            self.assertEqual(len(list(root.glob("*.png"))), 1)

    def test_store_evicts_oldest_asset_at_byte_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_body = b"\x89PNG\r\n\x1a\nold-image"
            new_body = b"\x89PNG\r\n\x1a\nnew-image"
            byte_quota = max(len(old_body), len(new_body))
            old_reference = store_thumbnail(
                root, old_body, "image/png", max_files=10, max_bytes=byte_quota
            )
            old_path = root / Path(old_reference).name
            os.utime(old_path, (1, 1))

            new_reference = store_thumbnail(
                root, new_body, "image/png", max_files=10, max_bytes=byte_quota
            )

            self.assertFalse(old_path.exists())
            self.assertTrue((root / Path(new_reference).name).is_file())
            self.assertLessEqual(
                sum(path.stat().st_size for path in root.glob("*.png")), byte_quota
            )

    def test_junction_component_is_rejected_when_platform_exposes_is_junction(self):
        if not callable(getattr(Path, "is_junction", None)):
            self.skipTest("Path.is_junction is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            anchor = Path(temporary) / "cache"
            root = anchor / "thumbnails"
            root.mkdir(parents=True)
            original = Path.is_junction

            def fake_is_junction(path):
                return path == root or original(path)

            with mock.patch.object(Path, "is_junction", fake_is_junction):
                with self.assertRaises(ValueError):
                    validate_thumbnail_root(root, anchor)


if __name__ == "__main__":
    unittest.main()
