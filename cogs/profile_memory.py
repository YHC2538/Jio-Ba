import json
import os
import re
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

PROFILE_MAX_ITEMS = 20
PROFILE_EVENT_HISTORY_LIMIT = 120

PROFILE_SCHEMA = {
    "dietary_preferences": ["likes", "dislikes"],
    "location_preferences": ["preferred_areas", "avoid_areas"],
    "time_preferences": ["preferred_slots", "avoid_slots"],
    "budget_preferences": ["preferred_ranges", "avoid_ranges"],
    "activity_preferences": ["likes", "dislikes"],
    "social_preferences": ["likes", "dislikes"],
}


def build_empty_profile() -> Dict[str, Dict[str, List[str]]]:
    profile = {}
    for section, buckets in PROFILE_SCHEMA.items():
        profile[section] = {bucket: [] for bucket in buckets}
    return profile


def build_empty_profile_meta() -> Dict[str, Any]:
    return {
        "version": 1,
        "last_updated_at": None,
        "processed_event_ids": [],
        "last_error": None,
    }


def _normalize_string_list(value: Any, max_items: int = PROFILE_MAX_ITEMS) -> List[str]:
    raw_list: List[str] = []
    if isinstance(value, str):
        raw_list = [value]
    elif isinstance(value, list):
        raw_list = [str(item or "") for item in value]
    elif value is None:
        raw_list = []
    else:
        raw_list = [str(value)]

    normalized: List[str] = []
    seen = set()
    for item in raw_list:
        cleaned = re.sub(r"\s+", " ", str(item or "").strip())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
        if len(normalized) >= max_items:
            break
    return normalized


def normalize_profile_update(payload: Any, max_items: int = PROFILE_MAX_ITEMS) -> Dict[str, Dict[str, List[str]]]:
    normalized: Dict[str, Dict[str, List[str]]] = {}
    if not isinstance(payload, dict):
        return normalized

    for section, buckets in PROFILE_SCHEMA.items():
        section_payload = payload.get(section)
        if not isinstance(section_payload, dict):
            continue
        for bucket in buckets:
            items = _normalize_string_list(section_payload.get(bucket), max_items=max_items)
            if items:
                normalized.setdefault(section, {})[bucket] = items
    return normalized


def merge_profiles(
    base_profile: Any,
    update_profile: Any,
    max_items: int = PROFILE_MAX_ITEMS,
) -> Dict[str, Dict[str, List[str]]]:
    base = normalize_profile_update(base_profile, max_items=max_items)
    update = normalize_profile_update(update_profile, max_items=max_items)

    merged = build_empty_profile()
    for section, buckets in PROFILE_SCHEMA.items():
        for bucket in buckets:
            values = []
            values.extend(base.get(section, {}).get(bucket, []))
            values.extend(update.get(section, {}).get(bucket, []))
            merged[section][bucket] = _normalize_string_list(values, max_items=max_items)
    return merged


def profile_hint_text(profile: Any, max_items_per_bucket: int = 3) -> str:
    normalized = merge_profiles(build_empty_profile(), profile)
    lines: List[str] = []

    def _take(values: List[str]) -> List[str]:
        return (values or [])[: max(max_items_per_bucket, 1)]

    diet = normalized["dietary_preferences"]
    if diet["likes"] or diet["dislikes"]:
        parts = []
        if diet["likes"]:
            parts.append(f"飲食偏好: {', '.join(_take(diet['likes']))}")
        if diet["dislikes"]:
            parts.append(f"飲食禁忌: {', '.join(_take(diet['dislikes']))}")
        lines.append("；".join(parts))

    location = normalized["location_preferences"]
    if location["preferred_areas"] or location["avoid_areas"]:
        parts = []
        if location["preferred_areas"]:
            parts.append(f"地點偏好: {', '.join(_take(location['preferred_areas']))}")
        if location["avoid_areas"]:
            parts.append(f"避免地點: {', '.join(_take(location['avoid_areas']))}")
        lines.append("；".join(parts))

    time_pref = normalized["time_preferences"]
    if time_pref["preferred_slots"] or time_pref["avoid_slots"]:
        parts = []
        if time_pref["preferred_slots"]:
            parts.append(f"時間偏好: {', '.join(_take(time_pref['preferred_slots']))}")
        if time_pref["avoid_slots"]:
            parts.append(f"避免時段: {', '.join(_take(time_pref['avoid_slots']))}")
        lines.append("；".join(parts))

    budget = normalized["budget_preferences"]
    if budget["preferred_ranges"] or budget["avoid_ranges"]:
        parts = []
        if budget["preferred_ranges"]:
            parts.append(f"預算偏好: {', '.join(_take(budget['preferred_ranges']))}")
        if budget["avoid_ranges"]:
            parts.append(f"避免預算: {', '.join(_take(budget['avoid_ranges']))}")
        lines.append("；".join(parts))

    activity = normalized["activity_preferences"]
    if activity["likes"] or activity["dislikes"]:
        parts = []
        if activity["likes"]:
            parts.append(f"活動偏好: {', '.join(_take(activity['likes']))}")
        if activity["dislikes"]:
            parts.append(f"避免活動: {', '.join(_take(activity['dislikes']))}")
        lines.append("；".join(parts))

    social = normalized["social_preferences"]
    if social["likes"] or social["dislikes"]:
        parts = []
        if social["likes"]:
            parts.append(f"社交偏好: {', '.join(_take(social['likes']))}")
        if social["dislikes"]:
            parts.append(f"社交禁忌: {', '.join(_take(social['dislikes']))}")
        lines.append("；".join(parts))

    return "\n".join(lines)


def build_profile_preference_fallback(profile: Any) -> Dict[str, str]:
    normalized = merge_profiles(build_empty_profile(), profile)
    fallback: Dict[str, str] = {}

    activity_likes = normalized["activity_preferences"]["likes"]
    location_likes = normalized["location_preferences"]["preferred_areas"]
    time_likes = normalized["time_preferences"]["preferred_slots"]
    budget_likes = normalized["budget_preferences"]["preferred_ranges"]
    social_likes = normalized["social_preferences"]["likes"]

    if activity_likes:
        fallback["core_what"] = activity_likes[0]
    if location_likes:
        fallback["core_where"] = location_likes[0]
    if time_likes:
        fallback["core_when"] = time_likes[0]

    if budget_likes:
        fallback["core_how"] = budget_likes[0]
    elif social_likes:
        fallback["core_how"] = social_likes[0]

    return fallback


def build_profile_dealbreakers(profile: Any, max_items: int = PROFILE_MAX_ITEMS) -> List[str]:
    normalized = merge_profiles(build_empty_profile(), profile, max_items=max_items)
    tokens: List[str] = []
    tokens.extend(normalized["dietary_preferences"]["dislikes"])
    tokens.extend(normalized["location_preferences"]["avoid_areas"])
    tokens.extend(normalized["time_preferences"]["avoid_slots"])
    tokens.extend(normalized["budget_preferences"]["avoid_ranges"])
    tokens.extend(normalized["activity_preferences"]["dislikes"])
    tokens.extend(normalized["social_preferences"]["dislikes"])
    return _normalize_string_list(tokens, max_items=max_items)


def _conversation_to_text(conversation: Any, max_items: int = 40) -> str:
    if not isinstance(conversation, list):
        return ""

    rendered: List[str] = []
    for item in conversation[-max_items:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "unknown").strip()
        parts = item.get("parts")
        if isinstance(parts, list):
            text = " ".join(str(part or "").strip() for part in parts if str(part or "").strip())
        else:
            text = str(item.get("content") or "").strip()
        if not text:
            continue
        rendered.append(f"[{role}] {text}")

    return "\n".join(rendered)


def _normalize_model_content(raw_content: Any) -> str:
    if raw_content is None:
        return ""
    if isinstance(raw_content, str):
        return raw_content.strip()
    if isinstance(raw_content, dict):
        for key in ("text", "output_text", "content"):
            value = raw_content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if isinstance(raw_content, list):
        parts: List[str] = []
        for item in raw_content:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())
                continue
            if isinstance(item, dict):
                for key in ("text", "output_text", "content"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
                        break
        return "\n".join(parts).strip()
    return str(raw_content).strip()


def _safe_json_parse(raw_content: Any) -> Dict[str, Any]:
    text = _normalize_model_content(raw_content)
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    if not text:
        return {}

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {}
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
            return {}
        except Exception:
            return {}


def _is_system_instruction_not_supported(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return (
        "developer instruction" in msg
        or "system instruction" in msg
        or ("system" in msg and "not enabled" in msg)
    )


async def extract_structured_profile_update(event: Dict[str, Any], participant: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {}

    interview = (participant or {}).get("interview", {}) or {}
    answers = interview.get("answers", {}) or {}
    conversation = participant.get("conversation_history", []) or []

    if not answers and not conversation:
        return {}

    llm = ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash"),
        google_api_key=api_key,
        temperature=0.1,
    )

    system_prompt = """
你是使用者輪廓分析師。請根據使用者在活動訪談中「明確表達」的內容，萃取長期偏好。
只允許輸出 JSON 物件，禁止任何額外文字。

輸出格式必須完全符合：
{
  "dietary_preferences": {"likes": [], "dislikes": []},
  "location_preferences": {"preferred_areas": [], "avoid_areas": []},
  "time_preferences": {"preferred_slots": [], "avoid_slots": []},
  "budget_preferences": {"preferred_ranges": [], "avoid_ranges": []},
  "activity_preferences": {"likes": [], "dislikes": []},
  "social_preferences": {"likes": [], "dislikes": []}
}

規則：
1. 只放入可追溯到原文的偏好，不可臆測。
2. 使用短句或關鍵詞，避免太長敘述。
3. 若無明確資訊，維持空陣列。
4. 若新回答與既有內容衝突，以本次最新明確回答為準。
5. 不要輸出 null，不要輸出其他欄位。
""".strip()

    user_prompt = (
        f"活動標題: {str((event or {}).get('title') or '').strip()}\n"
        f"活動摘要: {str((event or {}).get('description') or '').strip()}\n"
        f"活動 seeds: {json.dumps((event or {}).get('activity_seeds') or {}, ensure_ascii=False)}\n"
        f"受訪者回答(answer map): {json.dumps(answers, ensure_ascii=False)}\n"
        f"受訪者對話紀錄:\n{_conversation_to_text(conversation)}"
    )

    trace_config = {
        "run_name": "user_profiling",
        "tags": ["jio-ba", "profile", "long-term-memory"],
        "metadata": {
            "event_id": str((event or {}).get("_id") or ""),
            "user_id": str((participant or {}).get("user_id") or ""),
        },
    }

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    try:
        resp = await llm.ainvoke(messages, config=trace_config)
    except Exception as exc:
        if not _is_system_instruction_not_supported(exc):
            raise
        fallback = [HumanMessage(content=f"{system_prompt}\n\n---\n{user_prompt}")]
        resp = await llm.ainvoke(fallback, config=trace_config)

    parsed = _safe_json_parse(getattr(resp, "content", ""))
    normalized = normalize_profile_update(parsed)
    if not normalized and (answers or conversation):
        response_preview = _normalize_model_content(getattr(resp, "content", ""))[:400]
        raise ValueError(f"user_profiling returned unparsable or empty structured JSON. preview={response_preview}")
    return normalized
