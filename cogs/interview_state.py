from typing import Any, Dict, List, Optional, TypedDict


class InterviewState(TypedDict, total=False):
    event_id: str
    user_id: str
    current_question_id: str
    answers: Dict[str, Any]
    questions: List[Dict[str, Any]]
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
    warning_count: int
    warn_issued: bool
    warning_user_reason: str
    reprompt_reason: str
    interview_completed: bool
    revision_count: int
    revision_target_qid: str
    revision_attempt: bool


def normalize_question_id(question_id: Optional[str], questions: List[Dict[str, Any]]) -> str:
    if not questions:
        return "completed"
    if not question_id:
        return questions[0]["id"]

    valid_ids = {item.get("id") for item in questions}
    if question_id in valid_ids:
        return question_id
    return questions[0]["id"]


def is_sufficient_answer(text: Any) -> bool:
    value = str(text or "").strip()
    if not value:
        return False

    low = value.lower()
    vague = {
        "隨便",
        "都可以",
        "都行",
        "都好",
        "沒差",
        "不知道",
        "you decide",
        "idk",
        "ok",
        "不知道欸",
    }
    if low in vague:
        return False

    if len(value) < 2:
        return False

    if value in {"今天", "明天", "下週", "這週"}:
        return False

    return True


def next_question_id(current_question_id: Optional[str], questions: List[Dict[str, Any]], answers: Dict[str, Any]) -> str:
    normalized = normalize_question_id(current_question_id, questions)
    if normalized == "completed":
        return "completed"

    for question in questions:
        qid = question.get("id")
        if not qid:
            continue
        if not is_sufficient_answer(answers.get(qid)):
            return qid

    return "completed"
