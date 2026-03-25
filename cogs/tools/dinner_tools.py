from langchain_core.tools import tool
from typing import List, Annotated
from langgraph.prebuilt import InjectedState

def get_dinner_tools(bot):
    """
    Returns a list of LangChain tools bound to the specific bot instance.
    """

    @tool
    async def send_message(user_id: int, content: str, event_id: str):
        """
        Send a direct message (DM) to a specific participant.
        Usage: send_message(user_id=123, content="Hello", event_id="...")
        """
        try:
            target_user = await bot.fetch_user(user_id)
            if target_user:
                # 1. Send DM
                await target_user.send(content)
                
                # 2. Persist to DB History
                db = bot.get_cog("Database")
                if db:
                    from bson import ObjectId
                    if ObjectId.is_valid(event_id):
                        await db.append_history(ObjectId(event_id), user_id, "model", content)
                    else:
                         print(f"[WARN] Invalid event_id {event_id} in send_message, history not saved.")
                
                return f"Successfully sent DM to User {user_id} and saved to history."
            return f"Failed: User {user_id} not found."
        except Exception as e:
            return f"Error sending DM to {user_id}: {str(e)}"

    @tool
    async def announce_to_channel(channel_id: int, content: str):
        """
        Send a public message to the event's channel.
        Use this for global announcements, status updates, or final conclusions.
        """
        try:
            channel = bot.get_channel(int(channel_id))
            if not channel:
                # Try fetch
                try:
                    channel = await bot.fetch_channel(int(channel_id))
                except:
                    pass
            
            if channel:
                await channel.send(content)
                return f"Successfully sent announcement to Channel {channel_id}."
            return f"Failed: Channel {channel_id} not found."
        except Exception as e:
            return f"Error announcing to {channel_id}: {str(e)}"

    @tool
    async def update_participant_db(event_id: str, user_id: int, status: str = None, add_constraints: List[str] = None, is_whatever: bool = None):
        """
        Update a participant's DB record.
        ALWAYS call this when you extract new information from a user.
        """
        db = bot.get_cog("Database")
        if not db: return "DB Error"
        
        from bson import ObjectId
        if not ObjectId.is_valid(event_id):
            return "Invalid Event ID"
            
        try:
            oid = ObjectId(event_id)
            if status:
                await db.update_participant_status(oid, user_id, status)
            if add_constraints:
                p = await db.get_participant(oid, user_id)
                current = p.get("constraints", []) if p else []
                new_set = set(current)
                new_set.update(add_constraints)
                await db.update_participant_constraints(oid, user_id, list(new_set))
                
            if is_whatever is not None:
                await db.set_participant_whatever(oid, user_id, is_whatever)
            
            # AUTO-UPDATE DASHBOARD to prevent race conditions
            jio_cog = bot.get_cog("Jio")
            if jio_cog:
                # Await to ensure visual consistency
                await jio_cog.update_dashboard(oid)

            return f"Updated User {user_id} in DB and refreshed Dashboard."
        except Exception as e:
            return f"DB Update Error: {e}"

    @tool
    async def search_restaurant(query: str, location: str):
        """
        Search for restaurant information from the web using AI with Google Search.
        Use this to find specific places, recommendations, or check operating hours/prices.
        """
        try:
            import os
            import google.generativeai as genai
            from google.api_core.client_options import ClientOptions
            
            # 1. Setup Gemini with Search Grounding
            api_key = os.getenv("GOOGLE_API_KEY")
            base_url = os.getenv("GOOGLE_API_ENDPOINT")
            
            if not api_key or not base_url:
                return "Error: GOOGLE_API_KEY or GOOGLE_API_ENDPOINT missing."

            # Configure SDK
            genai.configure(
                api_key=api_key,
                client_options=ClientOptions(api_endpoint=base_url),
                transport="rest"
            )
            
            model = genai.GenerativeModel('gemini-3-flash-preview')

            # 2. Construct Prompt
            prompt = f"""
            Please help me find restaurant information.
            Query: {query}
            Location: {location}
            
            Conditions:
            1. Recommend specific restaurants matching the query.
            2. If specific conditions are provided in the query (e.g. price, type), respect them.
            3. OUTPUT FORMAT: PURE JSON ONLY. No markdown, no intro.
            
            JSON Structure:
            [
                {{
                    "name": "Restaurant Name",
                    "address": "Address",
                    "rating": "Google Rating (if available)",
                    "price_range": "Price info",
                    "reason": "Why it matches"
                }}
            ]
            """
            
            # 3. Invoke with tools
            # Note: We run this in a thread executor because genai SDK might be synchronous or blocking
            import asyncio
            import functools
            
            loop = asyncio.get_running_loop()
            
            def run_genai():
                return model.generate_content(
                    prompt,
                    tools='google_search_retrieval'
                )
            
            response = await loop.run_in_executor(None, run_genai)
            
            # 4. Parse Response
            content = response.text
            # Cleanup code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].strip()
                
            return f"Search Results (JSON): {content}"

        except Exception as e:
            return f"Search Error: {str(e)}"

    @tool
    async def update_dashboard(event_id: str):
        """
        Update the main event dashboard (the recruit message) in the public channel.
        Call this whenever a user's status changes (e.g., JOINED -> INTERVIEWING -> READY).
        """
        jio_cog = bot.get_cog("Jio")
        if not jio_cog: return "Error: Jio Cog not found."
        
        try:
            # We assume event_id is string from LLM
            from bson import ObjectId
            if not ObjectId.is_valid(event_id): return "Invalid Event ID"
            
            await jio_cog.update_dashboard(ObjectId(event_id))
            return "Dashboard updated."
        except Exception as e:
            return f"Error updating dashboard: {e}"

    @tool
    async def conclude_event(event_id: str, conclusion: str):
        """
        Finalize the event with a conclusion.
        Call this when a decision has been reached. 
        This will:
        1. Announce the conclusion to the channel.
        2. Mark the event as FINISHED in DB (if logic added).
        """
        # 1. Announce
        jio_cog = bot.get_cog("Jio")
        if not jio_cog: return "Error: Jio Cog not found."
        
        try:
            from bson import ObjectId
            if not ObjectId.is_valid(event_id): return "Invalid Event ID"
            oid = ObjectId(event_id)
            
            db = bot.get_cog("Database")
            event = await db.get_event(oid)
            if not event: return "Event not found"
            
            channel = bot.get_channel(event["channel_id"])
            if channel:
                await channel.send(f"🎉 **聚餐結論**: {conclusion}")
            
            return "Event concluded and announced."
        except Exception as e:
            return f"Error concluding event: {e}"

    @tool
    async def batch_send_messages(messages: List[dict], event_id: str, force: bool = False):
        """
        Send DMs to multiple participants in a batch.
        Args:
            messages: List of dicts, each having {"user_id": int, "content": str}
            event_id: The event ID string.
            force: Set to True to BYPASS anti-nagging checks (CRITICAL announcements only).
        """
        if not messages: return "No messages to send."
        
        results = []
        db = bot.get_cog("Database")
        from bson import ObjectId
        
        if not ObjectId.is_valid(event_id):
             return "Invalid Event ID"
        oid = ObjectId(event_id)

        # [NEW] Pre-fetch participant statuses for Anti-Nagging
        participant_status = {}
        if db:
            event = await db.get_event(oid)
            if event:
                for p in event.get("participants", []):
                    participant_status[p["user_id"]] = p.get("last_question_status", "NONE")

        # [NEW] Group by content to minimize history entries
        # Key: content, Value: list of user_ids
        content_map = {}
        
        # 1. Send DMs
        for msg in messages:
            uid = msg.get("user_id")
            content = msg.get("content")
            if not uid or not content: 
                results.append(f"Skipped invalid msg: {msg}")
                continue
            
            # [ANTI-NAGGING CHECK]
            # If user is ALREADY waiting for reply, do not send another message.
            # Unless we are explicitly resetting status (which set_phase does, but this tool doesn't know).
            curr_stat = participant_status.get(uid, "NONE")
            if not force and curr_stat == "WAITING_FOR_REPLY":
                results.append(f"Skipped sending to {uid} (Already WAITING_FOR_REPLY - Anti-Nag)")
                continue

            # Grouping for logging
            if content not in content_map:
                content_map[content] = []
            content_map[content].append(uid)
            
            try:
                target_user = await bot.fetch_user(uid)
                if target_user:
                    await target_user.send(content)
                    if db:
                        # Update Reply Status
                        await db.set_participant_reply_status(oid, uid, "WAITING_FOR_REPLY")
                    results.append(f"Sent to {uid}")
                else:
                    results.append(f"User {uid} not found")
            except Exception as e:
                results.append(f"Error sending to {uid}: {e}")

        # 2. Log to Unified History (Grouped)
        if db:
            for content, uids in content_map.items():
                # Append one history entry for this content aimed at multiple users
                await db.append_history(oid, None, "model", content, targets=uids)

        return "Batch Send Results: " + ", ".join(results)

    @tool
    async def set_phase(event_id: str, phase: str):
        """
        advance the event phase.
        Valid phases: 'LOGISTICS', 'CUISINE', 'CONCLUSION'.
        Call this when the previous phase is 'settled' (consensus reached).
        """
        valid_phases = ["LOGISTICS", "CUISINE", "CONCLUSION"]
        if phase not in valid_phases:
            return f"Invalid phase. Must be one of {valid_phases}"
            
        db = bot.get_cog("Database")
        if not db: return "DB Error"
        
        from bson import ObjectId
        if not ObjectId.is_valid(event_id): return "Invalid Event ID"
        
        await db.set_event_phase(ObjectId(event_id), phase)
        return f"Event Phase transitioned to {phase}."

    @tool
    async def manage_tasks(action: str, content: str, event_id: Annotated[str, InjectedState("event_id")]):
        """
        Manage dynamic To-Do list.
        Args:
            action: 'add', 'complete', 'delete'
            content: Task description (if adding) or Task ID (if completing/deleting).
            event_id: (Injected) The event ID.
        """
        db = bot.get_cog("Database")
        if not db: return "DB Error"
        
        from bson import ObjectId
        if not ObjectId.is_valid(event_id): return "Invalid Event ID"
        
        result = await db.manage_task(ObjectId(event_id), action, content)
        return result

    @tool
    async def random_pick(options: List[str]):
        """
        Randomly select one item from the provided list of options.
        Use this when Decision Mode is 'RANDOM' or when users ask to pick randomly.
        """
        import random
        if not options: return "Error: No options provided."
        choice = random.choice(options)
        return f"Randomly selected: {choice}"

    @tool
    async def kick_participant(event_id: str, user_id: int, reason: str = "Reason not specified"):
        """
        Kick a participant from the event.
        Use this when a user is being actively disruptive, trolling, or refusing to cooperate after multiple attempts.
        This will set their status to 'KICKED' and they will effectively be removed from the decision process.
        """
        db = bot.get_cog("Database")
        if not db: return "DB Error"
        
        from bson import ObjectId
        if not ObjectId.is_valid(event_id): return "Invalid Event ID"
        oid = ObjectId(event_id)
        
        # 1. Update Status
        await db.update_participant_status(oid, user_id, "KICKED")
        
        # 2. Update Dashboard
        jio_cog = bot.get_cog("Jio")
        if jio_cog:
            await jio_cog.update_dashboard(oid)
            
        # 3. Notify User (Optional but polite)
        try:
            target_user = await bot.fetch_user(user_id)
            if target_user:
                await target_user.send(f"🚫 您已被移除出聚餐活動。\n原因: {reason}")
        except:
            pass
            
        return f"User {user_id} has been KICKED. Reason: {reason}"

    return [batch_send_messages, announce_to_channel, update_participant_db, search_restaurant, conclude_event, set_phase, manage_tasks, random_pick, kick_participant]
