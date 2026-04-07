import unittest

from cogs.profile_memory import (
    _normalize_model_content,
    _safe_json_parse,
    build_profile_dealbreakers,
    build_profile_preference_fallback,
    merge_profiles,
    normalize_profile_update,
    profile_hint_text,
)


class TestProfileMemory(unittest.TestCase):
    def test_normalize_model_content_from_list_blocks(self):
        raw = [
            {"type": "text", "text": "```json\n{\"dietary_preferences\": {\"likes\": [\"壽司\"], \"dislikes\": []}}\n```"},
        ]
        text = _normalize_model_content(raw)
        self.assertIn("dietary_preferences", text)

    def test_safe_json_parse_from_list_blocks(self):
        raw = [
            {"type": "text", "text": "```json\n{\"time_preferences\": {\"preferred_slots\": [\"平日晚上\"], \"avoid_slots\": []}}\n```"},
        ]
        parsed = _safe_json_parse(raw)
        self.assertEqual(parsed.get("time_preferences", {}).get("preferred_slots"), ["平日晚上"])

    def test_normalize_profile_update_keeps_schema_only(self):
        raw = {
            "dietary_preferences": {
                "likes": ["義大利麵", " 義大利麵 ", "壽司"],
                "dislikes": ["羊肉爐", ""],
                "unknown": ["x"],
            },
            "location_preferences": {
                "preferred_areas": "台北市區",
                "avoid_areas": ["太遠", "太遠"],
            },
            "not_allowed": {"foo": ["bar"]},
        }

        normalized = normalize_profile_update(raw)
        self.assertEqual(normalized["dietary_preferences"]["likes"], ["義大利麵", "壽司"])
        self.assertEqual(normalized["dietary_preferences"]["dislikes"], ["羊肉爐"])
        self.assertEqual(normalized["location_preferences"]["preferred_areas"], ["台北市區"])
        self.assertNotIn("not_allowed", normalized)

    def test_merge_profiles_and_fallback(self):
        base = {
            "activity_preferences": {"likes": ["桌遊"], "dislikes": []},
            "location_preferences": {"preferred_areas": ["台北"], "avoid_areas": []},
            "time_preferences": {"preferred_slots": ["平日晚上"], "avoid_slots": []},
            "budget_preferences": {"preferred_ranges": ["300-500"], "avoid_ranges": []},
            "social_preferences": {"likes": ["小團"], "dislikes": []},
            "dietary_preferences": {"likes": [], "dislikes": ["羊肉爐"]},
        }
        update = {
            "activity_preferences": {"likes": ["桌遊", "電影"]},
            "dietary_preferences": {"dislikes": ["羊肉爐", "香菜"]},
        }

        merged = merge_profiles(base, update)
        fallback = build_profile_preference_fallback(merged)
        dealbreakers = build_profile_dealbreakers(merged)

        self.assertEqual(merged["activity_preferences"]["likes"], ["桌遊", "電影"])
        self.assertEqual(fallback.get("core_what"), "桌遊")
        self.assertEqual(fallback.get("core_where"), "台北")
        self.assertEqual(fallback.get("core_when"), "平日晚上")
        self.assertEqual(fallback.get("core_how"), "300-500")
        self.assertIn("羊肉爐", dealbreakers)
        self.assertIn("香菜", dealbreakers)

    def test_profile_hint_text(self):
        profile = {
            "dietary_preferences": {"likes": ["壽司"], "dislikes": ["羊肉爐"]},
            "location_preferences": {"preferred_areas": ["台北市區"], "avoid_areas": []},
            "time_preferences": {"preferred_slots": ["平日 19:00 後"], "avoid_slots": []},
            "budget_preferences": {"preferred_ranges": ["中價位"], "avoid_ranges": []},
            "activity_preferences": {"likes": ["聚餐"], "dislikes": []},
            "social_preferences": {"likes": ["4-6 人"], "dislikes": []},
        }

        hint = profile_hint_text(profile)
        self.assertIn("飲食偏好", hint)
        self.assertIn("飲食禁忌", hint)
        self.assertIn("地點偏好", hint)
        self.assertIn("時間偏好", hint)


if __name__ == "__main__":
    unittest.main()
