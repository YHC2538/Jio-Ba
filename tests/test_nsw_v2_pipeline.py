import unittest

from cogs.matching.nsw_calculator import (
    CandidatePlan,
    SEMANTIC_UTILITY_FLOOR,
    UserProfile,
    rank_candidates_from_matrix,
    utility_breakdown,
)


class TestNSWV2Pipeline(unittest.TestCase):
    def test_hard_penalty_floor(self):
        user = UserProfile(
            user_id=10,
            preferences={"core_what": "聚餐", "core_where": "台北", "core_when": "週五晚上", "core_how": "平價"},
            dealbreakers=["酒吧"],
            weights={},
        )
        candidate = CandidatePlan(what="酒吧聚會", where="台北", when="週五晚上", budget="平價")

        details = utility_breakdown(user, candidate, dealbreaker_penalty=SEMANTIC_UTILITY_FLOOR)

        self.assertTrue(details["dealbreaker_hit"])
        self.assertEqual(details["overall"], SEMANTIC_UTILITY_FLOOR)
        self.assertEqual(details["dimensions"]["what"], SEMANTIC_UTILITY_FLOOR)

    def test_rank_candidates_from_matrix_prefers_fair_plan(self):
        candidates = [
            CandidatePlan(what="折衷方案", where="中山", when="週五晚", budget="平價"),
            CandidatePlan(what="偏好單方", where="信義", when="週五晚", budget="平價"),
        ]
        utility_matrix = [
            [{"user_id": 1, "utility": 0.6}, {"user_id": 2, "utility": 0.6}],
            [{"user_id": 1, "utility": 1.0}, {"user_id": 2, "utility": 0.1}],
        ]

        ranked = rank_candidates_from_matrix(candidates, utility_matrix, top_k=2)

        self.assertEqual(ranked[0]["candidate"]["what"], "折衷方案")
        self.assertGreater(ranked[0]["nsw_score"], ranked[1]["nsw_score"])


if __name__ == "__main__":
    unittest.main()
