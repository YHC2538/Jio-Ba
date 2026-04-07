from dataclasses import dataclass
from functools import reduce
from itertools import product
from typing import Any, Dict, Iterable, List, Sequence, Tuple

DEALBREAKER_PENALTY = 0.01
SEMANTIC_UTILITY_FLOOR = 0.05
DEFAULT_WEIGHTS = {"when": 0.35, "where": 0.3, "what": 0.2, "how": 0.15}


@dataclass
class CandidatePlan:
    what: str
    where: str
    when: str
    budget: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "what": self.what,
            "where": self.where,
            "when": self.when,
            "budget": self.budget,
        }


@dataclass
class UserProfile:
    user_id: int
    preferences: Dict[str, Any]
    dealbreakers: List[str]
    weights: Dict[str, float]


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()

# dealbreaker 的意思是「底線」，也就是說如果候選方案中包含了使用者的 dealbreaker
# 這個方案就會被嚴重扣分（在這裡是直接給予一個很低的分數 DEALBREAKER_PENALTY），以反映出這個方案對該使用者來說幾乎是不可接受的。
def _contains_dealbreaker(candidate: CandidatePlan, dealbreakers: Sequence[str]) -> bool:
    haystack = " ".join(candidate.as_dict().values()).lower()
    for token in dealbreakers:
        if _norm(token) and _norm(token) in haystack:
            return True
    return False


def has_dealbreaker(candidate: CandidatePlan, dealbreakers: Sequence[str]) -> bool:
    return _contains_dealbreaker(candidate, dealbreakers)


def _field_score(preferred_value: Any, candidate_value: str) -> float:
    preferred_text = _norm(preferred_value)
    candidate_text = _norm(candidate_value)

    if not preferred_text:
        return 1.0

    if preferred_text == candidate_text:
        return 1.0

    if preferred_text in candidate_text or candidate_text in preferred_text:
        return 0.7

    return 0.25


def utility_breakdown(
    user: UserProfile,
    candidate: CandidatePlan,
    dealbreaker_penalty: float = DEALBREAKER_PENALTY,
) -> Dict[str, Any]:
    if _contains_dealbreaker(candidate, user.dealbreakers):
        return {
            "overall": dealbreaker_penalty,
            "dealbreaker_hit": True,
            "dimensions": {
                "what": dealbreaker_penalty,
                "where": dealbreaker_penalty,
                "when": dealbreaker_penalty,
                "how": dealbreaker_penalty,
            },
        }

    merged_weights = dict(DEFAULT_WEIGHTS)
    merged_weights.update(user.weights or {})

    pref = user.preferences or {}
    when_pref = pref.get("core_when") or pref.get("when")
    where_pref = pref.get("core_where") or pref.get("where")
    what_pref = pref.get("core_what") or pref.get("what")
    how_pref = pref.get("core_how") or pref.get("how") or pref.get("budget")

    dimensions = {
        "when": _field_score(when_pref, candidate.when),
        "where": _field_score(where_pref, candidate.where),
        "what": _field_score(what_pref, candidate.what),
        "how": _field_score(how_pref, candidate.budget),
    }

    score = 0.0
    score += merged_weights.get("when", 0.0) * dimensions["when"]
    score += merged_weights.get("where", 0.0) * dimensions["where"]
    score += merged_weights.get("what", 0.0) * dimensions["what"]
    score += merged_weights.get("how", 0.0) * dimensions["how"]

    return {
        "overall": max(score, dealbreaker_penalty),
        "dealbreaker_hit": False,
        "dimensions": dimensions,
    }


def utility(user: UserProfile, candidate: CandidatePlan) -> float:
    details = utility_breakdown(user, candidate, dealbreaker_penalty=DEALBREAKER_PENALTY)
    return float(details["overall"])


def nsw_score(users: Sequence[UserProfile], candidate: CandidatePlan) -> float:
    if not users:
        return 0.0

    utilities = [utility(user, candidate) for user in users]
    return reduce(lambda acc, value: acc * value, utilities, 1.0)


def generate_candidates(
    what_options: Iterable[str],
    where_options: Iterable[str],
    when_options: Iterable[str],
    budget_options: Iterable[str],
) -> List[CandidatePlan]:
    candidates = []
    for what, where, when, budget in product(what_options, where_options, when_options, budget_options):
        candidates.append(CandidatePlan(what=what, where=where, when=when, budget=budget))
    return candidates


def rank_candidates(users: Sequence[UserProfile], candidates: Sequence[CandidatePlan], top_k: int = 2) -> List[Dict[str, Any]]:
    scored: List[Tuple[CandidatePlan, float, List[Dict[str, Any]]]] = []

    for candidate in candidates:
        per_user = []
        for user in users:
            per_user.append({"user_id": user.user_id, "utility": utility(user, candidate)})
        score = nsw_score(users, candidate)
        scored.append((candidate, score, per_user))

    scored.sort(key=lambda item: item[1], reverse=True)

    output = []
    for candidate, score, per_user in scored[: max(top_k, 1)]:
        output.append(
            {
                "candidate": candidate.as_dict(),
                "nsw_score": score,
                "per_user_utility": per_user,
            }
        )

    return output


def rank_candidates_from_matrix(
    candidates: Sequence[CandidatePlan],
    utility_matrix: Sequence[Sequence[Dict[str, Any]]],
    top_k: int = 2,
) -> List[Dict[str, Any]]:
    scored: List[Tuple[CandidatePlan, float, float, float, int, List[Dict[str, Any]]]] = []

    for idx, candidate in enumerate(candidates):
        per_user = list(utility_matrix[idx]) if idx < len(utility_matrix) else []
        utilities = [float(item.get("utility", 0.0) or 0.0) for item in per_user]
        score = reduce(lambda acc, value: acc * value, utilities, 1.0) if utilities else 0.0
        min_utility = min(utilities) if utilities else 0.0
        avg_utility = sum(utilities) / len(utilities) if utilities else 0.0
        scored.append((candidate, score, min_utility, avg_utility, idx, per_user))

    scored.sort(key=lambda item: (item[1], item[2], item[3], -item[4]), reverse=True)

    output: List[Dict[str, Any]] = []
    for candidate, score, min_utility, avg_utility, _, per_user in scored[: max(top_k, 1)]:
        output.append(
            {
                "candidate": candidate.as_dict(),
                "nsw_score": score,
                "min_utility": min_utility,
                "avg_utility": avg_utility,
                "per_user_utility": per_user,
            }
        )

    return output
