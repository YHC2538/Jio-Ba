import motor.motor_asyncio
from discord.ext import commands
import os

class Database(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo_uri = os.getenv("MONGO_URI", "mongodb+srv://dian_user:Ql2AWvIW4pZPR2G3@discordeat.d2zjvdv.mongodb.net/?appName=discordEat")
        self.client = motor.motor_asyncio.AsyncIOMotorClient(self.mongo_uri)
        self.db = self.client["wei_jia_ba_bot"]
        self.users = self.db["users"]
        self.events = self.db["events"]
        print(f"Connected to MongoDB at {self.mongo_uri}")

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

    # Event Methods
    async def update_event_message_id(self, event_id, message_id):
        await self.events.update_one(
            {"_id": event_id},
            {"$set": {"message_id": message_id}}
        )

    async def create_event(self, initiator_id, description, channel_id, title="未命名聚餐", decision_mode="AI", message_id=None):
        event_doc = {
            "initiator_id": initiator_id,
            "title": title,
            "decision_mode": decision_mode,
            "description": description, # e.g. "Dinner at 7pm in Taipei"
            "active": True,
            "channel_id": channel_id,
            "message_id": message_id,
            "participants": [], # List of {user_id, status, ...}
            "global_constraints": [],
            "pending_questions": [], # List of {user_id, question, priority}
            "conversation_history": [], # [NEW] Global Unified Timeline
            "current_phase": "LOGISTICS", # [NEW] Phase State Machine (LOGISTICS -> CUISINE -> CONCLUSION)
            "tasks": [] # [NEW] Dynamic To-Do List [{"id": uuid, "content": str, "status": "PENDING"}]
        }
        result = await self.events.insert_one(event_doc)
        return result.inserted_id

    async def get_event(self, event_id):
        return await self.events.find_one({"_id": event_id})

    # Participant Methods
    async def add_participant(self, event_id, user_id):
        event = await self.events.find_one({"_id": event_id})
        if not event: return
        
        participant_ids = [p["user_id"] for p in event.get("participants", [])]
        if user_id not in participant_ids:
            participant_doc = {
                "user_id": user_id,
                "status": "PENDING", # PENDING, JOINED, DECLINED, KICKED, FINISHED
                "constraints": [],
                "conversation_history": [], # List of {role: "user"/"model", parts: ["text"]}
                "is_whatever": False,
                "current_topic": "when",
                "preferences": {},
                "dealbreakers": [],
                "interview_completed": False,
                "is_malicious": False
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

    async def update_participant_constraints(self, event_id, user_id, constraints):
        # Constraints is a list of strings
        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            {"$set": {"participants.$.constraints": constraints}}
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
    
    async def set_participant_whatever(self, event_id, user_id, is_whatever):
        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            {"$set": {"participants.$.is_whatever": is_whatever}}
        )

    async def set_participant_reply_status(self, event_id, user_id, status):
        """
        Update the last_question_status for a participant.
        status: "WAITING_FOR_REPLY", "REPLIED", or "NONE"
        """
        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            {"$set": {"participants.$.last_question_status": status}}
        )

    async def update_participant_interview(
        self,
        event_id,
        user_id,
        preferences=None,
        dealbreakers=None,
        current_topic=None,
        interview_completed=None,
        is_malicious=None,
    ):
        set_fields = {}

        if preferences is not None:
            set_fields["participants.$.preferences"] = preferences
        if dealbreakers is not None:
            set_fields["participants.$.dealbreakers"] = dealbreakers
        if current_topic is not None:
            set_fields["participants.$.current_topic"] = current_topic
        if interview_completed is not None:
            set_fields["participants.$.interview_completed"] = interview_completed
        if is_malicious is not None:
            set_fields["participants.$.is_malicious"] = is_malicious

        if not set_fields:
            return

        await self.events.update_one(
            {"_id": event_id, "participants.user_id": user_id},
            {"$set": set_fields}
        )

    # Question Table Methods
    async def add_questions(self, event_id, questions):
        # questions: list of {target_user_id, question, from_user_id}
        if not questions: return
        
        import datetime
        now = datetime.datetime.utcnow()
        to_add = []
        for q in questions:
            to_add.append({
                "target_user_id": q["target_user_id"],
                "question": q["question"],
                "from_user_id": q.get("from_user_id", "AI"),
                "status": "PENDING",
                "created_at": now
            })
            
        await self.events.update_one(
            {"_id": event_id},
            {"$push": {"pending_questions": {"$each": to_add}}}
        )

    async def get_next_pending_question(self, event_id, user_id):
        # Find first PENDING question for this user
        event = await self.events.find_one(
            {"_id": event_id, "pending_questions": {"$elemMatch": {"target_user_id": user_id, "status": "PENDING"}}},
            {"pending_questions.$": 1}
        )
        if event and event.get("pending_questions"):
            return event["pending_questions"][0]
        return None

    async def mark_question_asked(self, event_id, user_id, question_text):
        # Update specific question status
        await self.events.update_one(
            {"_id": event_id, "pending_questions": {"$elemMatch": {"target_user_id": user_id, "status": "PENDING"}}},
            {"$set": {"pending_questions.$.status": "ASKED"}}
        )

    # [NEW] Phase Methods
    async def set_event_phase(self, event_id, phase):
        await self.events.update_one(
            {"_id": event_id},
            {
                "$set": {"current_phase": phase, "participants.$[].last_question_status": "NONE"}
            }
        )

    # [NEW] Task Management Methods
    async def manage_task(self, event_id, action, content):
        """
        action: 'add', 'complete', 'delete'
        content: task description (add) or task_id (complete/delete)
        """
        import uuid
        
        if action == "add":
            task_id = str(uuid.uuid4())[:8] # Short ID
            task = {
                "id": task_id,
                "content": content,
                "status": "PENDING"
            }
            await self.events.update_one(
                {"_id": event_id},
                {"$push": {"tasks": task}}
            )
            return f"Task Added: [{task_id}] {content}"
            
        elif action == "complete":
            # Find and update status
            await self.events.update_one(
                {"_id": event_id, "tasks.id": content},
                {"$set": {"tasks.$.status": "COMPLETED"}}
            )
            return f"Task Completed: {content}"
            
        elif action == "delete":
            await self.events.update_one(
                {"_id": event_id},
                {"$pull": {"tasks": {"id": content}}}
            )
            return f"Task Deleted: {content}"
        
        return "Invalid Action"

def setup(bot):
    bot.add_cog(Database(bot))
