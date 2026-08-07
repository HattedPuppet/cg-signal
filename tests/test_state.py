import unittest

from cg_signal.storage import normalize_user_state


class UserStateTests(unittest.TestCase):
    def test_state_is_deduplicated_and_sanitized(self):
        state = normalize_user_state(
            {
                "read": ["a", "a", "b", 7],
                "saved": ["saved-1"],
                "archived": "not-a-list",
            }
        )
        self.assertNotIn("read", state)
        self.assertEqual(state["saved"], ["saved-1"])
        self.assertEqual(state["archived"], [])
        self.assertEqual(state["notes"], {})
        self.assertEqual(state["feedback"], [])
        self.assertEqual(state["muted_sources"], [])
        self.assertEqual(state["reduced_sources"], [])
        self.assertIn("updated_at", state)

    def test_preferences_notes_and_feedback_are_sanitized(self):
        state = normalize_user_state(
            {
                "notes": {"article-1": "  Keep this reference.  ", "bad": 7},
                "feedback": [
                    {
                        "id": "article-1",
                        "value": 1,
                        "source_id": "80-level",
                        "software_tags": ["Unreal Engine", "Unreal Engine"],
                        "topic_tags": ["Lighting & rendering"],
                    },
                    {"id": "article-2", "value": 0},
                ],
                "muted_sources": ["noisy-source"],
                "reduced_sources": ["quiet-source", "noisy-source"],
            }
        )
        self.assertEqual(state["notes"], {"article-1": "Keep this reference."})
        self.assertEqual(len(state["feedback"]), 1)
        self.assertEqual(state["feedback"][0]["value"], 1)
        self.assertEqual(state["feedback"][0]["software_tags"], ["Unreal Engine"])
        self.assertEqual(state["muted_sources"], ["noisy-source"])
        self.assertEqual(state["reduced_sources"], ["quiet-source"])

    def test_unknown_fields_are_not_persisted(self):
        state = normalize_user_state({"read": [], "admin": True})
        self.assertNotIn("admin", state)
        self.assertNotIn("read", state)

    def test_updated_at_is_bounded_and_must_be_an_iso_timestamp(self):
        oversized = normalize_user_state({"updated_at": "x" * 1000})
        malformed = normalize_user_state({"updated_at": "not-a-date"})
        self.assertLessEqual(len(oversized["updated_at"]), 64)
        self.assertNotEqual(malformed["updated_at"], "not-a-date")


if __name__ == "__main__":
    unittest.main()
