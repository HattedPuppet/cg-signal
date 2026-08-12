import unittest

from cg_signal.storage import normalize_user_state


class UserStateTests(unittest.TestCase):
    def test_state_is_exact_saved_muted_updated_shape(self):
        state = normalize_user_state({
            "saved": ["saved-1", "saved-1", 7],
            "muted_sources": ["noisy", "noisy", 7],
            "notes": {"old": "discard"},
            "feedback": [{"id": "old", "value": 1}],
        })
        self.assertEqual(set(state), {"saved", "muted_sources", "updated_at"})
        self.assertEqual(state["saved"], ["saved-1"])
        self.assertEqual(state["muted_sources"], ["noisy"])

    def test_unknown_and_obsolete_fields_are_not_emitted(self):
        state = normalize_user_state({"archived": ["x"], "reduced_sources": ["quiet"], "admin": True})
        self.assertEqual(state["saved"], [])
        self.assertEqual(state["muted_sources"], [])
        self.assertNotIn("archived", state)
        self.assertNotIn("reduced_sources", state)
        self.assertNotIn("feedback", state)
        self.assertNotIn("notes", state)

    def test_updated_at_is_bounded_and_must_be_iso(self):
        oversized = normalize_user_state({"updated_at": "x" * 1000})
        malformed = normalize_user_state({"updated_at": "not-a-date"})
        self.assertLessEqual(len(oversized["updated_at"]), 64)
        self.assertNotEqual(malformed["updated_at"], "not-a-date")


if __name__ == "__main__":
    unittest.main()
