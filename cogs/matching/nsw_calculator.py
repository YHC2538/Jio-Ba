from dataclasses import dataclass
from functools import reduce
from itertools import product
from typing import Any, Dict, Iterable, List, Sequence, Tuple

DEALBREAKER_PENALTY = 0.01
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


def _contains_dealbreaker(candidate: CandidatePlan, dealbreakers: Sequence[str]) -> bool:
    haystack = " ".join(candidate.as_dict().values()).lower()
    for token in dealbreakers:
        if _norm(token) and _norm(token) in haystack:
            return True
    return False


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


def utility(user: UserProfile, candidate: CandidatePlan) -> float:
    if _contains_dealbreaker(candidate, user.dealbreakers):
        return DEALBREAKER_PENALTY

    merged_weights = dict(DEFAULT_WEIGHTS)
    merged_weights.update(user.weights or {})

    score = 0.0
    pref = user.preferences or {}

    when_pref = pref.get("core_when") or pref.get("when")
    where_pref = pref.get("core_where") or pref.get("where")
    what_pref = pref.get("core_what") or pref.get("what")
    how_pref = pref.get("core_how") or pref.get("how") or pref.get("budget")

    score += merged_weights.get("when", 0.0) * _field_score(when_pref, candidate.when)
    score += merged_weights.get("where", 0.0) * _field_score(where_pref, candidate.where)
    score += merged_weights.get("what", 0.0) * _field_score(what_pref, candidate.what)
    score += merged_weights.get("how", 0.0) * _field_score(how_pref, candidate.budget)

    return max(score, DEALBREAKER_PENALTY)


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
