import os
import datetime
import json
from urllib.parse import urlparse

import motor.motor_asyncio
from discord.ext import commands
from langchain_google_genai import ChatGoogleGenerativeAI


def _mask_mongo_uri(uri: str) -> str:
    if not uri:
        return "<empty>"
    try:
        parsed = urlparse(uri)
        host = parsed.hostname or "<unknown-host>"
        scheme = parsed.scheme or "mongodb"
        return f"{scheme}://***@{host}"
    except Exception:
        return "<invalid-uri>"

class Database(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo_uri = os.getenv("MONGO_URI", "mongodb+srv://dian_user:Ql2AWvIW4pZPR2G3@discordeat.d2zjvdv.mongodb.net/?appName=discordEat")
        self.client = motor.motor_asyncio.AsyncIOMotorClient(
            self.mongo_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
        )
        self.db = self.client["jio_ba_bot"]
        self.users = self.db["users"]
        self.events = self.db["events"]
        print(f"MongoDB client initialized: {_mask_mongo_uri(self.mongo_uri)}")

    # User Methods
    async def get_user(self, user_id):
        return await self.users.find_one({"_id": user_id})

    async def create_user(self, user_id):
        await self.users.update_one(
            {"_id": user_id},
            {"$setOnInsert": {"strikes": 0, "ping_allowed": True}},
            upsert=True
        )

    async def add_strike(self, user_id):
        await self.users.update_one({"_id": user_id}, {"$inc": {"strikes": 1}})
        user = await self.get_user(user_id)
        return user["strikes"] if user else 0

    async def set_user_active_event(self, user_id, event_id):
        await self.users.update_one(
            {"_id": user_id},
            {
                "$set": {"active_event_id": str(event_id)},
                "$setOnInsert": {"strikes": 0, "ping_allowed": True}
            },
            upsert=True
        )

    async def get_user_active_event(self, user_id):
        user = await self.get_user(user_id)
        if not user:
            return None
        return user.get("active_event_id")

    async def clear_user_active_event(self, user_id):
        await self.users.update_one(
            {"_id": user_id},
            {"$unset": {"active_event_id": ""}}
        )

    async def clear_user_active_event_if_matches(self, user_id, event_id):
        await self.users.update_one(
            {"_id": user_id, "active_event_id": str(event_id)},
            {"$unset": {"active_event_id": ""}}
        )

    # Event Methods
    async def update_event_message_id(self, event_id, message_id):
        await self.events.update_one(
            {"_id": event_id},
            {"$set": {"message_id": message_id}}
        )

    async def _build_interview_questions(self, title=None, description=None, seeds=None, custom_questions=None):
        seeds = seeds or {}
        custom_questions = custom_questions or []

        fallback_questions = {
            "what": "這次活動你最想做的內容是什麼？",
            "where": "你偏好的活動地點或區域在哪裡？",
            "when": "你可參與的時間區間是什麼？",
            "how": "你希望活動怎麼進行（節奏、方式、分工）？",
        }

        asked_topics = [topic for topic in ["what", "where", "when", "how"] if not seeds.get(topic)]
        generated_questions = {topic: fallback_questions[topic] for topic in asked_topics}

        if asked_topics:
            api_key = os.getenv("GOOGLE_API_KEY")
            if api_key:
                try:
                    llm = ChatGoogleGenerativeAI(
                        model=os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash"),
                        google_api_key=api_key,
                        temperature=0.2,
                    )

                    prompt = f"""
你是活動訪綱設計助手。請依據活動資訊，為尚未決定的主題生成提問句。

活動標題: {str(title or '').strip()}
活動描述: {str(description or '').strip()}
已預設 seeds: {json.dumps({k: seeds.get(k) for k in ['what','where','when','how']}, ensure_ascii=False)}
需提問主題: {asked_topics}

請輸出 JSON，key 只能是 what/where/when/how，value 是一句繁體中文問題。
規則：
1) 每題都要可直接回答，不要空泛。
2) 不要產生 why 題。
3) 只輸出 JSON。
"""
                    resp = await llm.ainvoke(prompt)
                    text = str(getattr(resp, "content", "") or "").strip()
                    if "```json" in text:
                        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
                    elif "```" in text:
                        text = text.split("```", 1)[1].split("```", 1)[0].strip()

                    parsed = {}
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = {}

                    for topic in asked_topics:
                        candidate = str((parsed or {}).get(topic) or "").strip()
                        if candidate:
                            generated_questions[topic] = candidate
                except Exception:
                    pass

        questions = []
        for topic in ["what", "where", "when", "how"]:
            if topic in asked_topics:
                question_text = generated_questions.get(topic) or fallback_questions[topic]
                questions.append({
                    "id": f"core_{topic}",
                    "topic": topic,
                    "text": question_text,
                    "required": True,
                })

        for idx, text in enumerate(custom_questions[:1], start=1):
            cleaned = str(text or "").strip()
            if not cleaned:
                continue
            questions.append(
                {
                    "id": f"custom_{idx}",
                    "topic": "custom",
                    "text": cleaned,
                    "required": True,
                }
            )

        return questions[:5]

    async def create_event(
        self,
        initiator_id,
        description,
        channel_id,
        title="未命名活動",
        message_id=None,
        signup_deadline=None,
        interview_deadline=None,
        min_participants=None,
        activity_seeds=None,
        custom_questions=None,
    ):
        activity_seeds = activity_seeds or {}
        custom_questions = custom_questions or []
        interview_questions = await self._build_interview_questions(
            title=title,
            description=description,
            seeds=activity_seeds,
            custom_questions=custom_questions,
        )

        host_participant = {
            "user_id": initiator_id,
            "role": "HOST",
            "status": "PENDING",
            "interview": {
                "current_question_id": interview_questions[0]["id"] if interview_questions else None,
                "answers": {},
                "answer_sources": {},
                "confirmed": False,
                "revision_count": 0,
                "edited_question_ids": [],
                "completed": False,
                "last_question_status": "NONE",
            },
            "dealbreakers": [],
            "warning_count": 0,
            "warning_reasons": [],
            "review_status": "NONE",
            "hold_context": None,
            "is_malicious": False,
            "joined_at": datetime.datetime.utcnow(),
            "notes": {"public": [], "private": []},
        }

        event_doc = {
            "schema_version": 2,
            "initiator_id": initiator_id,
            "title": title,
            "description": description,
            "active": True,
            "channel_id": channel_id,
            "message_id": message_id,
            "signup_deadline": signup_deadline,
            "interview_deadline": interview_deadline,
            "min_participants": int(min_participants) if min_participants else None,
            "activity_type": "GENERAL",
            "activity_seeds": {
                "what": activity_seeds.get("what"),
                "where": activity_seeds.get("where"),
                "when": activity_seeds.get("when"),
                "how": activity_seeds.get("how"),
            },
            "host_custom_questions": custom_questions[:1],
            "interview_questions": interview_questions,
            "workflow_state": "RECRUITING",
            "cancelled": False,
            "cancel_reason": None,
            "cancelled_by": None,
            "cancelled_at": None,
            "adjudication_status": "PENDING",
            "adjudication_result": None,
            "adjudication_by": None,
            "adjudication_at": None,
            "scheduled_event_id": None,
            "adjudication_candidates": [],
            "participants": [host_participant],
            "conversation_history": [],
            "warning_policy": {
                "threshold": 5,
                "max_final_revisions": 1,
            },
        }
        result = await self.events.insert_one(event_doc)
        return result.inserted_id

    def active_participant_count(self, event):
        participants = (event or {}).get("participants", []) or []
        active_statuses = {"PENDING", "INTERVIEWING", "READY", "ON_HOLD"}
        return sum(1 for p in participants if p.get("status") in active_statuses)

    async def cancel_event(self, event_id, cancelled_by, reason=""):
        event = await self.get_event(event_id)
        if not event:
            return None

        now = datetime.datetime.utcnow()
        await self.events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "active": False,
                    "cancelled": True,
                    "cancel_reason": str(reason or "").strip() or "Host cancelled",
                    "cancelled_by": cancelled_by,
                    "cancelled_at": now,
                    "workflow_state": "CANCELLED",
                    "adjudication_status": "CANCELLED",
                }
            },
        )
        return await self.get_event(event_id)

    async def fail_event_min_participants(self, event_id):
        event = await self.get_event(event_id)
        if not event:
            return None

        min_required = event.get("min_participants")
        if not min_required:
            return None

        current = self.active_participant_count(event)
        if current >= int(min_required):
            return None

        now = datetime.datetime.utcnow()
        reason = f"Minimum participants not met ({current}/{int(min_required)})"
        await self.events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "active": False,
                    "cancelled": True,
                    "cancel_reason": reason,
                    "cancelled_by": "SYSTEM",
                    "cancelled_at": now,
                    "workflow_state": "FAILED_MIN_PARTICIPANTS",
                    "adjudication_status": "CANCELLED",
                }
            },
        )
        return await self.get_event(event_id)

    def ready_plus_host_count(self, event):
        participants = (event or {}).get("participants", []) or []
        count = 0
        for participant in participants:
            role = participant.get("role")
            status = participant.get("status")
            if role == "HOST":
                count += 1
                continue
            if status == "READY":
                count += 1
        return count

    async def fail_event_min_participants_by_ready(self, event_id):
        event = await self.get_event(event_id)
        if not event:
            return None

        min_required = event.get("min_participants")
        if not min_required:
            return None

        current = self.ready_plus_host_count(event)
        if current >= int(min_required):
            return None

        now = datetime.datetime.utcnow()
        reason = f"Minimum participants not met after interview deadline ({current}/{int(min_required)})"
        await self.events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "active": False,
                    "cancelled": True,
                    "cancel_reason": reason,
                    "cancelled_by": "SYSTEM",
                    "cancelled_at": now,
                    "workflow_state": "FAILED_MIN_PARTICIPANTS",
                    "adjudication_status": "CANCELLED",
                }
            },
        )
        return await self.get_event(event_id)

    async def get_on_hold_participants(self, event_id):
        event = await self.get_event(event_id)
        if not event:
            return []
        return [
            p for p in event.get("participants", [])
            if p.get("status") == "ON_HOLD" or p.get("review_status") == "ON_HOLD"
        ]

    async def get_event(self, event_id):
        return await self.events.find_one({"_id": event_id})

    async def find_conflicting_interview_event(self, user_id, exclude_event_id=None):
        query = {
            "cancelled": {"$ne": True},
            "workflow_state": {"$nin": ["CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"]},
            "adjudication_status": {"$nin": ["DECIDED", "CANCELLED"]},
            "participants": {
                "$elemMatch": {
                    "user_id": user_id,
                    "status": "INTERVIEWING",
                    "interview.completed": {"$ne": True},
                }
            },
        }
        if exclude_event_id is not None:
            query["_id"] = {"$ne": exclude_event_id}
        return await self.events.find_one(query, sort=[("_id", -1)])

    # Participant Methods
    async def add_participant(self, event_id, user_id):
        event = await self.events.find_one({"_id": event_id})
        if not event: return
        
        participant_ids = [p["user_id"] for p in event.get("participants", [])]
        if user_id not in participant_ids:
            questions = event.get("interview_questions", [])
            participant_doc = {
                "user_id": user_id,
                "role": "PARTICIPANT",
                "status": "PENDING",
                "interview": {
                    "current_question_id": questions[0]["id"] if questions else None,
                    "answers": {},
                    "answer_sources": {},
                    "confirmed": False,
                    "revision_count": 0,
                    "edited_question_ids": [],
                    "completed": False,
                    "last_question_status": "NONE",
                },
                "dealbreakers": [],
                "warning_count": 0,
                "warning_reasons": [],
                "review_status": "NONE",
                "hold_context": None,
                "is_malicious": False,
                "joined_at": datetime.datetime.utcnow(),
                "notes": {"public": [], "private": []},
            }
            await self.events.update_one(
                {"_id": event_id},
                {"$push": {"participants": participant_doc}}
            )

    async def get_participant(self, event_id, user_id):
        event = await self.events.find_one(
            {"_id": event_id, "participants.user_id": user_id},
            {"participants.$": 1}
        )
        if event and event.get("participants"):
            return event["participants"][0]
        return None

    async def update_participant_status(self, event_id, user_id, status):
        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            {"$set": {"participants.$.status": status}}
        )

    async def append_history(self, event_id, user_id, role, content, author_name=None, targets=None):
        # role: "user" or "model" or "system"
        import datetime
        now = datetime.datetime.utcnow()
        
        msg = {
            "role": role, 
            "parts": [content],
            "timestamp": now.isoformat(),
            "author_id": user_id if role == "user" else "AI",
            "author_name": author_name or ("User" if role == "user" else "AI"),
            "targets": targets or [] # [NEW] List of user_ids this message is directed to
        }
        
        # [MODIFIED] Push to GLOBAL event history
        await self.events.update_one(
            {"_id": event_id},
            {"$push": {"conversation_history": msg}}
        )
    
    async def set_participant_reply_status(self, event_id, user_id, status):
        """
        Update the last_question_status for a participant.
        status: "WAITING_FOR_REPLY", "REPLIED", or "NONE"
        """
        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            {"$set": {"participants.$.interview.last_question_status": status}}
        )

    async def update_participant_interview(
        self,
        event_id,
        user_id,
        answers=None,
        dealbreakers=None,
        current_question_id=None,
        interview_completed=None,
        confirmed=None,
        revision_count=None,
        edited_question_ids=None,
        is_malicious=None,
    ):
        set_fields = {}

        if answers is not None:
            set_fields["participants.$.interview.answers"] = answers
        if dealbreakers is not None:
            set_fields["participants.$.dealbreakers"] = dealbreakers
        if current_question_id is not None:
            set_fields["participants.$.interview.current_question_id"] = current_question_id
        if interview_completed is not None:
            set_fields["participants.$.interview.completed"] = interview_completed
        if confirmed is not None:
            set_fields["participants.$.interview.confirmed"] = confirmed
        if revision_count is not None:
            set_fields["participants.$.interview.revision_count"] = revision_count
        if edited_question_ids is not None:
            set_fields["participants.$.interview.edited_question_ids"] = edited_question_ids
        if is_malicious is not None:
            set_fields["participants.$.is_malicious"] = is_malicious

        if not set_fields:
            return

        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            {"$set": set_fields}
        )

    async def get_event_revision_limit(self, event_id):
        event = await self.get_event(event_id)
        if not event:
            return 2
        policy = event.get("warning_policy", {}) or {}
        return int(policy.get("max_final_revisions", 2))

    async def append_participant_note(self, event_id, user_id, note, note_type="public", max_items=20):
        cleaned = str(note or "").strip()
        if not cleaned:
            return False

        note_key = "participants.$.notes.public"
        if str(note_type).strip().lower() == "private":
            note_key = "participants.$.notes.private"

        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            {
                "$push": {
                    note_key: {
                        "$each": [cleaned],
                        "$slice": -int(max_items),
                    }
                }
            },
        )
        return True

    async def increment_participant_warning(self, event_id, user_id, reason, context=None):
        event = await self.get_event(event_id)
        threshold = int(((event or {}).get("warning_policy", {}) or {}).get("threshold", 5))

        participant = await self.get_participant(event_id, user_id)
        if not participant:
            return None

        # Keep warning counter event-scoped and capped to threshold.
        if int(participant.get("warning_count", 0) or 0) >= threshold:
            return participant

        update = {
            "$inc": {"participants.$.warning_count": 1},
            "$push": {"participants.$.warning_reasons": reason},
        }
        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            update,
        )

        participant = await self.get_participant(event_id, user_id)
        if not participant:
            return None

        if participant.get("warning_count", 0) >= threshold:
            hold_context = {
                "reason": reason,
                "summary": f"Warning threshold reached: {reason}",
            }
            if isinstance(context, dict):
                hold_context.update(
                    {
                        "question": str(context.get("question") or "").strip(),
                        "reply": str(context.get("reply") or "").strip(),
                        "participant_reason": str(context.get("participant_reason") or "").strip(),
                    }
                )

            await self.events.update_one(
                {"_id": event_id, "participants.user_id": user_id},
                {
                    "$set": {
                        "participants.$.review_status": "ON_HOLD",
                        "participants.$.status": "ON_HOLD",
                        "participants.$.hold_context": hold_context,
                    }
                },
            )
            participant = await self.get_participant(event_id, user_id)
        return participant

    async def decrement_participant_warning(self, event_id, user_id, reason="MODEL_FALSE_POSITIVE", context=None):
        event = await self.get_event(event_id)
        threshold = int(((event or {}).get("warning_policy", {}) or {}).get("threshold", 5))

        participant = await self.get_participant(event_id, user_id)
        if not participant:
            return None

        current = int(participant.get("warning_count", 0) or 0)
        if current <= 0:
            return participant

        next_count = max(0, current - 1)
        set_fields = {
            "participants.$.warning_count": next_count,
        }

        if participant.get("review_status") == "ON_HOLD" and next_count < threshold:
            set_fields["participants.$.review_status"] = "CONTINUE"
            if participant.get("status") == "ON_HOLD":
                set_fields["participants.$.status"] = "INTERVIEWING"
                set_fields["participants.$.interview.last_question_status"] = "WAITING_FOR_REPLY"
            set_fields["participants.$.hold_context"] = {
                "verdict": "CONTINUE",
                "reason": f"Warning retracted by agent: {reason}",
            }

        update = {
            "$set": set_fields,
            "$push": {"participants.$.warning_reasons": f"RETRACT:{reason}"},
        }

        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            update,
        )

        if isinstance(context, dict):
            note = str(context.get("note") or "").strip()
            if note:
                await self.append_participant_note(event_id, user_id, note, note_type="private")

        return await self.get_participant(event_id, user_id)

    async def apply_host_verdict(self, event_id, user_id, verdict, reason=""):
        normalized = str(verdict or "").strip().upper()
        if normalized not in {"CONTINUE", "KICK"}:
            return False

        if normalized == "CONTINUE":
            await self.events.update_one(
                {"_id": event_id, "participants.user_id": user_id},
                {
                    "$set": {
                        "participants.$.review_status": "CONTINUE",
                        "participants.$.status": "INTERVIEWING",
                        "participants.$.interview.last_question_status": "WAITING_FOR_REPLY",
                        "participants.$.hold_context": {
                            "verdict": "CONTINUE",
                            "reason": reason,
                        },
                    }
                },
            )
            return True

        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            {
                "$set": {
                    "participants.$.review_status": "KICKED",
                    "participants.$.status": "KICKED",
                    "participants.$.hold_context": {
                        "verdict": "KICK",
                        "reason": reason,
                    },
                }
            },
        )
        return True

    async def auto_kick_interview_overdue(self, event_id):
        event = await self.get_event(event_id)
        if not event:
            return []

        kicked_ids = []
        for participant in event.get("participants", []):
            if participant.get("status") in {"READY", "KICKED", "DECLINED", "FINISHED"}:
                continue
            if participant.get("interview", {}).get("completed"):
                continue

            uid = participant.get("user_id")
            if uid is None:
                continue

            kicked_ids.append(uid)
            await self.events.update_one(
                {"_id": event_id, "participants.user_id": uid},
                {
                    "$set": {
                        "participants.$.status": "KICKED",
                        "participants.$.review_status": "DEADLINE_TIMEOUT",
                        "participants.$.hold_context": {
                            "verdict": "KICK",
                            "reason": "Interview deadline reached",
                        },
                    }
                },
            )

        return kicked_ids

def setup(bot):
    bot.add_cog(Database(bot))
