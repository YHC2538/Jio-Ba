from typing import TypedDict, Dict, Any, List, Optional


TOPIC_SEQUENCE = ["when", "where", "what", "budget"]
VALID_TOPICS = set(TOPIC_SEQUENCE + ["completed"])


class InterviewState(TypedDict, total=False):
    event_id: str
    user_id: str
    current_topic: str
    preferences: Dict[str, Any]
    dealbreakers: List[str]
    chat_history: List[Dict[str, Any]]
    is_malicious: bool
    user_inputs: Dict[int, str]
    participants: Dict[int, Dict[str, Any]]
    extracted: Dict[str, Any]
    route: str
    needs_tool: bool
    tool_request: Dict[str, Any]
    tool_result: str
    outbound_messages: List[Dict[str, Any]]
    malicious_reason: str


def normalize_topic(topic: Optional[str]) -> str:
    if not topic:
        return "when"
    lowered = str(topic).strip().lower()
    if lowered not in VALID_TOPICS:
        return "when"
    return lowered


def next_topic(current_topic: Optional[str], preferences: Dict[str, Any]) -> str:
    normalized = normalize_topic(current_topic)
    if normalized == "completed":
        return "completed"

    for topic in TOPIC_SEQUENCE:
        value = preferences.get(topic)
        if not value:
            return topic

    return "completed"
