import json
import os
import re
from typing import Any, Dict, List, Tuple

from bson import ObjectId
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph

from cogs.interview_state import InterviewState, is_sufficient_answer, next_question_id, normalize_question_id
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


def _question_lookup(questions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(item.get("id")): item for item in questions or [] if item.get("id")}


def _render_form(questions: List[Dict[str, Any]], answers: Dict[str, Any]) -> str:
    lines = ["🧾 Interview Form", ""]
    for idx, question in enumerate(questions or [], start=1):
        qid = str(question.get("id"))
        text = question.get("text", "")
        value = str(answers.get(qid) or "(未填)")
        lines.append(f"{idx}. {text}")
        lines.append(f"   ↳ {value}")
    return "\n".join(lines)


def _render_form_embed(questions: List[Dict[str, Any]], answers: Dict[str, Any]) -> Dict[str, Any]:
    fields = []
    for idx, question in enumerate(questions or [], start=1):
        qid = str(question.get("id"))
        qtext = str(question.get("text") or "")
        avalue = str(answers.get(qid) or "(未填)")
        if len(avalue) > 900:
            avalue = avalue[:880] + "..."
        fields.append(
            {
                "name": f"{idx}. {qtext}"[:256],
                "value": avalue,
                "inline": False,
            }
        )

    return {
        "title": "🧾 Interview Form",
        "description": "目前已記錄的訪談答案",
        "color": 0x5865F2,
        "fields": fields[:25],
    }


def _parse_revision_update(user_text: str, questions: List[Dict[str, Any]]) -> Tuple[str, str]:
    text = str(user_text or "").strip()
    if not text:
        return "", ""

    qmap = _question_lookup(questions)

    match = re.match(r"^\s*(\d+)\s*[:：=]\s*(.+)$", text)
    if match:
        idx = int(match.group(1))
        value = match.group(2).strip()
        if 1 <= idx <= len(questions):
            return str(questions[idx - 1].get("id") or ""), value
        return "", ""

    match = re.match(r"^\s*([a-zA-Z_]+)\s*[:：=]\s*(.+)$", text)
    if match:
        raw_key = match.group(1).strip().lower()
        value = match.group(2).strip()
        if raw_key in qmap:
            return raw_key, value
        alias = {
            "what": "core_what",
            "where": "core_where",
            "when": "core_when",
            "how": "core_how",
        }
        mapped = alias.get(raw_key)
        if mapped and mapped in qmap:
            return mapped, value

    return "", ""


def _looks_like_clarification(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    markers = ["哪一題", "哪題", "什麼題", "看不懂", "不懂", "請再說明", "再說一次", "which question"]
    return any(m in lowered for m in markers)


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

        questions: List[Dict[str, Any]] = []
        answers: Dict[str, Any] = {}
        current_question_id = None
        dealbreakers: List[str] = []
        interview_completed = False
        revision_count = 0

        event = None
        participant = None
        if db and ObjectId.is_valid(event_id):
            event = await db.get_event(ObjectId(event_id))
            participant = await db.get_participant(ObjectId(event_id), user_id)
            if event:
                questions = event.get("interview_questions", []) or []
            if participant:
                interview = participant.get("interview", {}) or {}
                answers = interview.get("answers", {}) or {}
                current_question_id = interview.get("current_question_id")
                interview_completed = bool(interview.get("completed"))
                revision_count = int(interview.get("revision_count", 0) or 0)
                dealbreakers = participant.get("dealbreakers", []) or []

        question_map = _question_lookup(questions)
        current_question_id = normalize_question_id(current_question_id, questions)

        if current_question_id == "completed":
            interview_completed = True

        current_question = question_map.get(str(current_question_id or ""), {})
        current_question_text = current_question.get("text", "目前沒有待回答問題")

        extraction_prompt = f"""
你是活動訪談資料整理器，請根據目前問題判斷使用者回答是否充分，並輸出 JSON。

活動標題: {(event or {}).get("title", "未命名活動")}
活動資訊: {(event or {}).get("description", "")}
活動預設 seeds: {json.dumps(((event or {}).get("activity_seeds", {}) or {}), ensure_ascii=False)}
目前問題ID: {current_question_id}
目前問題內容: {current_question_text}
使用者輸入: {user_text}

輸出格式（只能 JSON）：
{{
  "answer_value": "",
  "sufficient": true/false,
  "off_topic": true/false,
  "dealbreakers": [],
  "assistant_reply": "",
    "missing_reason": "",
  "private_note": ""
}}

規則：
1) sufficient=true 代表可以用來回答目前問題，不可過度寬鬆。
2) 若是過於含糊（例如：隨便、都可以、今天）通常 sufficient=false。
3) dealbreakers 可為任何活動限制，不限飲食。
4) assistant_reply 使用繁體中文，禮貌且簡潔。
5) missing_reason 說明目前回答缺什麼，讓使用者知道如何補充。
6) private_note 如無內容請給空字串。
"""

        response = await llm.ainvoke(extraction_prompt)
        parsed = _safe_json_parse(getattr(response, "content", ""))

        extracted_answer = str(parsed.get("answer_value") or user_text or "").strip()
        sufficient = bool(parsed.get("sufficient")) and is_sufficient_answer(extracted_answer)
        off_topic = bool(parsed.get("off_topic"))
        reprompt_reason = str(parsed.get("missing_reason") or "").strip()

        revision_target_qid = ""
        revision_value = ""
        if interview_completed:
            revision_target_qid, revision_value = _parse_revision_update(user_text, questions)
            if revision_target_qid and is_sufficient_answer(revision_value):
                answers[str(revision_target_qid)] = revision_value
                sufficient = True
                off_topic = False

        if sufficient and current_question_id and current_question_id != "completed":
            answers[str(current_question_id)] = extracted_answer

        merged_dealbreakers = list(dict.fromkeys(dealbreakers + (parsed.get("dealbreakers", []) or [])))
        next_qid = next_question_id(current_question_id, questions, answers)

        route = "finalize"
        if not sufficient or off_topic:
            route = "reprompt"

        warning_reason = ""
        warning_count = 0
        warn_issued = False
        warning_user_reason = ""
        if route == "reprompt" and db and ObjectId.is_valid(event_id) and user_id and not interview_completed:
            should_warn = False
            warning_reason = "clarification_needed"
            warning_user_reason = "目前回答和題目還不夠對焦，請先補充題目需要的具體資訊。"

            if not _looks_like_clarification(user_text):
                warning_prompt = f"""
你是訪談風險分類器。請嚴格判斷是否需要發出 warning。

只有在以下情況才可 should_warn=true：
- 明顯惡意內容
- 明確與當前問題嚴重不相關，且有刻意拖延跡象
- 明確拒絕合作且連續不配合

以下都不應該 warning：
- 參與者在詢問澄清（例如：哪一題、可否再解釋、不太懂）
- 一般性理解困難但仍願意配合

目前問題: {current_question_text}
使用者訊息: {user_text}

輸出 JSON：
{{
  "should_warn": true/false,
    "reason": "MALICIOUS|SEVERE_MISMATCH|DELAYING|NON_COOPERATIVE|CONFUSED|NORMAL",
    "participant_reason": "給參與者看的繁中一句話理由"
}}

規則：
1) 只有在證據充分時才 should_warn=true。
2) participant_reason 必須具體指出問題，不可空泛。
3) 只能輸出 JSON。
"""
                warning_resp = await llm.ainvoke(warning_prompt)
                warning_json = _safe_json_parse(getattr(warning_resp, "content", ""))
                should_warn = bool(warning_json.get("should_warn"))
                warning_reason = str(warning_json.get("reason") or "off_topic_or_insufficient")
                warning_user_reason = str(warning_json.get("participant_reason") or warning_user_reason).strip()

            if should_warn:
                warn_issued = True
                updated = await db.increment_participant_warning(ObjectId(event_id), user_id, warning_reason)
                warning_count = (updated or {}).get("warning_count", 0)
                if (updated or {}).get("review_status") == "ON_HOLD":
                    route = "hold"

        if interview_completed and route == "reprompt":
            warning_reason = "revision_format_invalid"

        return {
            "user_id": str(user_id),
            "current_question_id": next_qid,
            "answers": answers,
            "questions": questions,
            "dealbreakers": merged_dealbreakers,
            "extracted": parsed,
            "warning_count": warning_count,
            "route": route,
            "malicious_reason": warning_reason,
            "warn_issued": warn_issued,
            "warning_user_reason": warning_user_reason,
            "reprompt_reason": reprompt_reason,
            "interview_completed": interview_completed,
            "revision_count": revision_count,
            "revision_target_qid": revision_target_qid,
            "revision_attempt": bool(revision_target_qid),
        }

    async def reprompt_node(state: InterviewState):
        event_id = state.get("event_id", "")
        user_id = int(state.get("user_id", "0") or 0)
        current_question_id = state.get("current_question_id")
        questions = state.get("questions", []) or []
        extracted = state.get("extracted", {}) or {}
        warning_count = int(state.get("warning_count", 0) or 0)
        warn_issued = bool(state.get("warn_issued"))
        warning_user_reason = str(state.get("warning_user_reason") or "").strip()
        reprompt_reason = str(state.get("reprompt_reason") or "").strip()
        interview_completed = bool(state.get("interview_completed"))
        _, latest_user_text = _latest_user_input(state.get("user_inputs", {}))
        qmap = _question_lookup(questions)
        current_question = qmap.get(str(current_question_id), {})

        if interview_completed:
            message = (
                "你目前已完成訪談。若要修訂答案，請用以下格式：\n"
                "`題號: 新答案` 或 `when=新答案`（what/where/when/how 也可）"
            )
        else:
            question_text = current_question.get("text") or "可否補充更完整的回答？"
            extracted_reply = str(extracted.get("assistant_reply") or "").strip()

            if extracted_reply:
                message = extracted_reply
            else:
                reprompt_prompt = f"""
你是活動訪談助理，目標是幫助參與者補齊目前問題，不要責備。

目前問題：{question_text}
使用者剛才回覆：{latest_user_text}
目前判定缺口：{reprompt_reason}

請輸出 2-4 句繁體中文：
1) 清楚指出哪裡不夠完整
2) 提供 1-2 個具體補充方向範例
3) 最後再次提醒目前題目

不要輸出 JSON。
"""
                reprompt_resp = await llm.ainvoke(reprompt_prompt)
                message = str(getattr(reprompt_resp, "content", "")).strip()
                if not message:
                    message = (
                        "我想確保有準確記錄你的偏好，剛剛的內容還不夠完整。\n"
                        f"請再補充這一題：{question_text}"
                    )

            if warn_issued:
                reason_line = warning_user_reason or "目前回答與題目嚴重不一致，且有延宕訪談風險。"
                message = (
                    f"⚠️ 訪談提醒（{warning_count}/2）：{reason_line}\n"
                    f"{message}"
                )

        if batch_send_messages_tool and event_id and user_id:
            await batch_send_messages_tool.ainvoke(
                {
                    "messages": [{"user_id": user_id, "content": message}],
                    "event_id": event_id,
                    "force": True,
                }
            )

        return {"user_inputs": {}, "outbound_messages": [{"user_id": user_id, "content": message}]}

    async def hold_node(state: InterviewState):
        db = bot.get_cog("Database")
        event_id = state.get("event_id", "")
        user_id = int(state.get("user_id", "0") or 0)

        if not db or not ObjectId.is_valid(event_id) or not user_id:
            return {"user_inputs": {}}

        event = await db.get_event(ObjectId(event_id))
        initiator_id = (event or {}).get("initiator_id")
        warning_user_reason = str(state.get("warning_user_reason") or "目前回覆和題目嚴重不符，且有延遲訪談風險。")
        warning_count = int(state.get("warning_count", 0) or 2)

        participant_msg = (
            f"⚠️ 你的回覆已達提醒門檻（{warning_count}/2），目前訪談已暫停。\n"
            f"原因：{warning_user_reason}\n"
            "請等待主揪裁決是否繼續。"
        )
        host_msg = (
            f"⚠️ 參與者 <@{user_id}> 已達提醒門檻（{warning_count}/2）。\n"
            f"判定原因：{warning_user_reason}\n"
            "請使用活動卡片的管理選單 -> 處理暫停名單，直接在 UI 裁決。"
        )

        if batch_send_messages_tool:
            await batch_send_messages_tool.ainvoke(
                {
                    "messages": [{"user_id": user_id, "content": participant_msg}],
                    "event_id": event_id,
                    "force": True,
                }
            )
            if initiator_id:
                await batch_send_messages_tool.ainvoke(
                    {
                        "messages": [{"user_id": initiator_id, "content": host_msg}],
                        "event_id": event_id,
                        "force": True,
                    }
                )

        return {"user_inputs": {}}

    async def finalize_node(state: InterviewState):
        db = bot.get_cog("Database")
        event_id = state.get("event_id", "")
        user_id = int(state.get("user_id", "0") or 0)

        answers = state.get("answers", {}) or {}
        questions = state.get("questions", []) or []
        dealbreakers = state.get("dealbreakers", []) or []
        current_question_id = state.get("current_question_id")
        extracted = state.get("extracted", {}) or {}
        interview_completed_before = bool(state.get("interview_completed"))
        revision_count = int(state.get("revision_count", 0) or 0)
        revision_attempt = bool(state.get("revision_attempt"))
        revision_target_qid = str(state.get("revision_target_qid") or "")

        completed = current_question_id == "completed"

        max_revisions = 2
        blocked_revision = False
        if db and ObjectId.is_valid(event_id):
            max_revisions = await db.get_event_revision_limit(ObjectId(event_id))

        if interview_completed_before and revision_attempt:
            if revision_count >= max_revisions:
                blocked_revision = True
            else:
                revision_count += 1

        if db and ObjectId.is_valid(event_id) and user_id and not blocked_revision:
            await db.update_participant_interview(
                ObjectId(event_id),
                user_id,
                answers=answers,
                dealbreakers=dealbreakers,
                current_question_id=current_question_id,
                interview_completed=completed,
                confirmed=completed,
                revision_count=revision_count,
                is_malicious=False,
            )

            private_note = str(extracted.get("private_note") or "").strip()
            if private_note:
                await db.append_participant_note(ObjectId(event_id), user_id, private_note, note_type="private")

            if revision_attempt and revision_target_qid:
                await db.append_participant_note(
                    ObjectId(event_id),
                    user_id,
                    f"修訂 {revision_target_qid}: {answers.get(revision_target_qid, '')}",
                    note_type="public",
                )

            if completed:
                await db.update_participant_status(ObjectId(event_id), user_id, "READY")

        form_embed = _render_form_embed(questions, answers)
        assistant_reply = str(extracted.get("assistant_reply") or "已更新你的回答。")

        if blocked_revision:
            if db and ObjectId.is_valid(event_id):
                persisted = await db.get_participant(ObjectId(event_id), user_id)
                persisted_answers = ((persisted or {}).get("interview", {}) or {}).get("answers", {}) or {}
                form_embed = _render_form_embed(questions, persisted_answers)
            assistant_reply = (
                f"你已達最終修訂上限（{max_revisions} 次），我會保留目前已確認版本。\n"
                "若仍需調整，請直接聯絡主揪處理。"
            )
        elif completed:
            assistant_reply += "\n\n✅ 你的訪談已完成，主揪將收到整合報告後裁決。"
            if interview_completed_before and revision_attempt:
                assistant_reply += f"\n🔁 已更新修訂（{revision_count}/{max_revisions}）。"
        else:
            qmap = _question_lookup(questions)
            next_q = qmap.get(str(current_question_id), {})
            next_q_text = next_q.get("text", "請回答下一題。")
            assistant_reply += f"\n\n下一題：{next_q_text}"

        if batch_send_messages_tool and event_id and user_id:
            await batch_send_messages_tool.ainvoke(
                {
                    "messages": [{"user_id": user_id, "content": assistant_reply, "embed": form_embed}],
                    "event_id": event_id,
                    "force": True,
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
            participant = await db.increment_participant_warning(ObjectId(event_id), user_id, f"malicious:{reason}")
            await db.update_participant_interview(
                ObjectId(event_id),
                user_id,
                is_malicious=True,
            )

            warning = "⚠️ 偵測到不安全或惡意訊息，該訊息已被攔截。"
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
                host_notice = f"🚨 參與者 <@{user_id}> 觸發安全攔截。原因：{reason}"
                if (participant or {}).get("review_status") == "ON_HOLD":
                    host_notice += "\n該參與者已進入 ON_HOLD，請使用管理選單 UI 裁決。"
                await batch_send_messages_tool.ainvoke(
                    {
                        "messages": [{"user_id": initiator_id, "content": host_notice}],
                        "event_id": event_id,
                        "force": True,
                    }
                )

        return {"user_inputs": {}}

    workflow = StateGraph(InterviewState)
    workflow.add_node("gatekeeper", gatekeeper_node)
    workflow.add_node("extractor", extractor_router_node)
    workflow.add_node("reprompt", reprompt_node)
    workflow.add_node("finalize", finalize_node)
    workflow.add_node("hold", hold_node)
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
            "reprompt": "reprompt",
            "finalize": "finalize",
            "hold": "hold",
        },
    )

    workflow.add_edge("finalize", END)
    workflow.add_edge("reprompt", END)
    workflow.add_edge("hold", END)
    workflow.add_edge("malicious", END)

    return workflow.compile()
