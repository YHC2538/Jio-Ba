import json
import os
import re
from typing import Any, Dict, Tuple

from bson import ObjectId
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph

from cogs.interview_state import InterviewState, next_topic, normalize_topic
from cogs.tools.dinner_tools import get_dinner_tools

load_dotenv()


DinnerState = InterviewState


def _safe_json_parse(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    if not text:
        return {}

    try:
        return json.loads(text)
    except Exception:
        return {}


def _latest_user_input(user_inputs: Dict[int, str]) -> Tuple[int, str]:
    if not user_inputs:
        return 0, ""
    last_uid = next(reversed(user_inputs))
    return int(last_uid), user_inputs[last_uid]


def is_prompt_injection(text: str) -> bool:
    lowered = (text or "").lower()
    injection_patterns = [
        r"ignore\s+(all|previous|above)\s+instructions",
        r"忽略(上述|之前|全部)指令",
        r"system\s+prompt",
        r"developer\s+message",
        r"修改.{0,12}(權重|weight).{0,8}(9999|999)",
        r"override\s+policy",
    ]
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in injection_patterns)


def create_graph(bot):
    tools = {tool.name: tool for tool in get_dinner_tools(bot)}
    search_restaurant_tool = tools.get("search_restaurant")
    batch_send_messages_tool = tools.get("batch_send_messages")

    gemini_api_key = os.getenv("GOOGLE_API_KEY")
    if not gemini_api_key:
        raise ValueError("Critical Error: GOOGLE_API_KEY is missing from environment variables.")

    llm = ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash"),
        google_api_key=gemini_api_key,
        temperature=0.2,
    )

    async def gatekeeper_node(state: InterviewState):
        user_id, text = _latest_user_input(state.get("user_inputs", {}))
        suspicious = is_prompt_injection(text)

        route = "extractor"
        malicious_reason = ""
        if suspicious:
            route = "malicious"
            malicious_reason = "Potential prompt injection detected by pattern check."

        return {
            "user_id": str(user_id),
            "is_malicious": suspicious,
            "malicious_reason": malicious_reason,
            "route": route,
        }

    async def extractor_router_node(state: InterviewState):
        db = bot.get_cog("Database")
        event_id = state.get("event_id", "")
        user_inputs = state.get("user_inputs", {})
        user_id, user_text = _latest_user_input(user_inputs)

        current_topic = "when"
        preferences: Dict[str, Any] = {}
        dealbreakers = []

        if db and ObjectId.is_valid(event_id):
            participant = await db.get_participant(ObjectId(event_id), user_id)
            if participant:
                current_topic = normalize_topic(participant.get("current_topic"))
                preferences = participant.get("preferences", {}) or {}
                dealbreakers = participant.get("dealbreakers", []) or []

        extraction_prompt = f"""
你是結構化資料萃取器，請將使用者訊息轉成 JSON。
current_topic={current_topic}
user_text={user_text}

輸出格式（只能 JSON）：
{{
  "topic_answered": true/false,
  "off_topic": true/false,
  "preferences_updates": {{"when":"", "where":"", "what":"", "budget":""}},
  "dealbreakers": [],
  "needs_tool": true/false,
  "tool_query": "",
  "tool_location": "",
  "assistant_reply": ""
}}

規則：
1) 若使用者明確表達禁忌（如「絕對不吃羊肉爐」），放入 dealbreakers。
2) 若文字包含「高CP值/便宜/省錢」可視為 budget 偏好。
3) 若內容偏離 current_topic，off_topic=true。
4) 若是 where 且地點太模糊，needs_tool=true。
5) assistant_reply 用繁體中文，1-2 句。
"""

        response = await llm.ainvoke(extraction_prompt)
        parsed = _safe_json_parse(getattr(response, "content", ""))

        normalized_text = (user_text or "").lower()
        if any(keyword in normalized_text for keyword in ["高cp", "高 cp", "便宜", "省錢", "划算"]):
            parsed.setdefault("preferences_updates", {})
            if not parsed["preferences_updates"].get("budget"):
                parsed["preferences_updates"]["budget"] = "高CP值"

        deterministic_dealbreakers = []
        for match in re.finditer(r"(?:絕對不吃|不吃|不能吃)([^，。,.\n]+)", user_text or ""):
            item = match.group(1).strip(" ：:、 ")
            if item:
                deterministic_dealbreakers.append(item)
        if deterministic_dealbreakers:
            parsed["dealbreakers"] = list(dict.fromkeys((parsed.get("dealbreakers", []) or []) + deterministic_dealbreakers))

        pref_updates = parsed.get("preferences_updates", {}) or {}
        merged_preferences = dict(preferences)
        for key in ["when", "where", "what", "budget"]:
            value = pref_updates.get(key)
            if value:
                merged_preferences[key] = value

        merged_dealbreakers = list(dict.fromkeys(dealbreakers + (parsed.get("dealbreakers", []) or [])))
        next_step_topic = next_topic(current_topic, merged_preferences)

        route = "finalize"
        if parsed.get("off_topic"):
            route = "reprompt"
        elif parsed.get("needs_tool"):
            route = "tool"

        return {
            "user_id": str(user_id),
            "current_topic": next_step_topic,
            "preferences": merged_preferences,
            "dealbreakers": merged_dealbreakers,
            "extracted": parsed,
            "needs_tool": bool(parsed.get("needs_tool")),
            "tool_request": {
                "query": parsed.get("tool_query") or user_text,
                "location": parsed.get("tool_location") or merged_preferences.get("where") or "台北市",
            },
            "route": route,
        }

    async def tool_node(state: InterviewState):
        if not search_restaurant_tool or not state.get("needs_tool"):
            return {"tool_result": ""}

        request = state.get("tool_request", {})
        try:
            tool_result = await search_restaurant_tool.ainvoke(
                {
                    "query": request.get("query", "聚餐推薦"),
                    "location": request.get("location", "台北市"),
                }
            )
            return {"tool_result": str(tool_result)}
        except Exception as error:
            return {"tool_result": f"Tool execution failed: {error}"}

    async def reprompt_node(state: InterviewState):
        event_id = state.get("event_id", "")
        user_id = int(state.get("user_id", "0") or 0)
        current_topic = normalize_topic(state.get("current_topic"))

        prompts = {
            "when": "我先幫你整理時間喔，這次你比較方便的時段是什麼？",
            "where": "了解！那你希望活動地點在哪一區或哪個交通樞紐附近？",
            "what": "收到，我們回到餐點偏好，你這次最想吃哪一類？",
            "budget": "最後確認預算，這次每人大約希望多少？",
            "completed": "你的偏好已經完成蒐集，稍後我會整合給主揪。",
        }
        message = prompts.get(current_topic, prompts["when"])

        if batch_send_messages_tool and event_id and user_id:
            await batch_send_messages_tool.ainvoke(
                {
                    "messages": [{"user_id": user_id, "content": message}],
                    "event_id": event_id,
                    "force": False,
                }
            )

        return {"user_inputs": {}, "outbound_messages": [{"user_id": user_id, "content": message}]}

    async def finalize_node(state: InterviewState):
        db = bot.get_cog("Database")
        event_id = state.get("event_id", "")
        user_id = int(state.get("user_id", "0") or 0)

        preferences = state.get("preferences", {})
        dealbreakers = state.get("dealbreakers", [])
        current_topic = normalize_topic(state.get("current_topic"))
        extracted = state.get("extracted", {})
        tool_result = state.get("tool_result", "")

        if db and ObjectId.is_valid(event_id) and user_id:
            await db.update_participant_interview(
                ObjectId(event_id),
                user_id,
                preferences=preferences,
                dealbreakers=dealbreakers,
                current_topic=current_topic,
                interview_completed=(current_topic == "completed"),
                is_malicious=False,
            )

            if current_topic == "completed":
                await db.update_participant_status(ObjectId(event_id), user_id, "READY")

        assistant_reply = extracted.get("assistant_reply") or "收到，我已經更新你的偏好。"
        if tool_result:
            assistant_reply = f"{assistant_reply}\n\n參考地點資訊：{tool_result[:400]}"

        if current_topic == "completed":
            assistant_reply += "\n\n✅ 你已完成訪談，接下來等待主揪裁決。"
        else:
            topic_prompt = {
                "when": "下一題：你方便的時間是？",
                "where": "下一題：你偏好活動地點在哪裡？",
                "what": "下一題：你想吃什麼類型？",
                "budget": "下一題：你的預算大約多少？",
            }
            assistant_reply += f"\n\n{topic_prompt.get(current_topic, '')}".rstrip()

        if batch_send_messages_tool and event_id and user_id:
            await batch_send_messages_tool.ainvoke(
                {
                    "messages": [{"user_id": user_id, "content": assistant_reply}],
                    "event_id": event_id,
                    "force": False,
                }
            )

        return {
            "user_inputs": {},
            "outbound_messages": [{"user_id": user_id, "content": assistant_reply}],
        }

    async def malicious_node(state: InterviewState):
        db = bot.get_cog("Database")
        event_id = state.get("event_id", "")
        user_id = int(state.get("user_id", "0") or 0)
        reason = state.get("malicious_reason", "Potential prompt injection")

        if db and ObjectId.is_valid(event_id) and user_id:
            event = await db.get_event(ObjectId(event_id))
            await db.update_participant_interview(
                ObjectId(event_id),
                user_id,
                is_malicious=True,
            )

            warning = "⚠️ 偵測到可能的惡意指令操作，這則訊息已被攔截，請改用一般需求描述。"
            if batch_send_messages_tool:
                await batch_send_messages_tool.ainvoke(
                    {
                        "messages": [{"user_id": user_id, "content": warning}],
                        "event_id": event_id,
                        "force": True,
                    }
                )

            initiator_id = event.get("initiator_id") if event else None
            if initiator_id and batch_send_messages_tool:
                await batch_send_messages_tool.ainvoke(
                    {
                        "messages": [
                            {
                                "user_id": initiator_id,
                                "content": f"🚨 參與者 <@{user_id}> 觸發 Gatekeeper 攔截。原因：{reason}",
                            }
                        ],
                        "event_id": event_id,
                        "force": True,
                    }
                )

        return {"user_inputs": {}}

    workflow = StateGraph(InterviewState)
    workflow.add_node("gatekeeper", gatekeeper_node)
    workflow.add_node("extractor", extractor_router_node)
    workflow.add_node("tool", tool_node)
    workflow.add_node("reprompt", reprompt_node)
    workflow.add_node("finalize", finalize_node)
    workflow.add_node("malicious", malicious_node)

    workflow.set_entry_point("gatekeeper")

    workflow.add_conditional_edges(
        "gatekeeper",
        lambda state: "malicious" if state.get("route") == "malicious" else "extractor",
    )

    workflow.add_conditional_edges(
        "extractor",
        lambda state: state.get("route", "finalize"),
        {
            "tool": "tool",
            "reprompt": "reprompt",
            "finalize": "finalize",
        },
    )

    workflow.add_edge("tool", "finalize")
    workflow.add_edge("finalize", END)
    workflow.add_edge("reprompt", END)
    workflow.add_edge("malicious", END)

    return workflow.compile()
