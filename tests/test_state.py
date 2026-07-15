import unittest

from server import normalize_user_state


class UserStateTests(unittest.TestCase):
    def test_state_is_deduplicated_and_sanitized(self):
        state = normalize_user_state(
            {
                "read": ["a", "a", "b", 7],
                "saved": ["saved-1"],
                "archived": "not-a-list",
            }
        )
        self.assertEqual(state["read"], ["a", "b"])
        self.assertEqual(state["saved"], ["saved-1"])
        self.assertEqual(state["archived"], [])
        self.assertIn("updated_at", state)

    def test_unknown_fields_are_not_persisted(self):
        state = normalize_user_state({"read": [], "admin": True})
        self.assertNotIn("admin", state)


if __name__ == "__main__":
    unittest.main()
