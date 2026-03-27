import os
import json
import asyncio
from discord.ext import commands
# Import the graph creator
from .graph_agent import create_graph, DinnerState


# --- Monkey Patch for Google GenAI Error Handling ---
# Fixes AttributeError when API returns a string error instead of dict
import google.genai.errors

def safe_get_message(self, response_json):
    try:
        if isinstance(response_json, str):
            return response_json
        return response_json.get('error', {}).get('message', None)
    except Exception:
        return str(response_json)

google.genai.errors.APIError._get_message = safe_get_message
# ----------------------------------------------------

class AIBrain(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Initialize the graph
        # Note: We need to ensure the graph is created with the bot instance for tools
        self.graph = create_graph(bot)
        
        # Per-user processing state.
        # queue_key format: "{event_id}:{user_id}"
        self.message_queues = {} # queue_key -> list of {"content": msg, "name": name}
        self.processing_tasks = {} # queue_key -> asyncio.Task
        self.locks = {} # lock_key -> asyncio.Lock (event_id:user_id or event_id)
        
        print("LangGraph Agent initialized with Batch Processing.")

    async def queue_message(self, event_id: str, user_id: int, content: str, user_name: str):
        """
        Add a message to the participant queue and ensure a processing task is running.
        """
        queue_key = f"{event_id}:{user_id}"

        if queue_key not in self.message_queues:
            self.message_queues[queue_key] = []

        self.message_queues[queue_key].append({
            "content": content,
            "name": user_name
        })
        print(
            f"[AIBrain] Queued message from {user_name} for Event {event_id} User {user_id}. "
            f"Queue size: {len(self.message_queues[queue_key])}"
        )

        # Start processing task if not running for this participant.
        if queue_key not in self.processing_tasks or self.processing_tasks[queue_key].done():
            self.processing_tasks[queue_key] = asyncio.create_task(self._process_loop(queue_key, event_id, user_id))

    async def _process_loop(self, queue_key: str, event_id: str, user_id: int):
        """
        Consumes one participant queue with debounce logic.
        """
        print(f"[AIBrain] Started process loop for {queue_key}")
        
        try:
            while True:
                if queue_key not in self.message_queues or not self.message_queues[queue_key]:
                    break

                # Per-user debounce avoids bot over-reply while still allowing cross-user parallelism.
                print(f"[AIBrain] Waiting 1.2s for debounce ({queue_key})...")
                await asyncio.sleep(1.2)

                queue = self.message_queues.get(queue_key, [])
                if not queue:
                    break

                batch_messages = list(queue)
                self.message_queues[queue_key] = []

                merged_text = "\n".join([str(item.get("content") or "").strip() for item in batch_messages if str(item.get("content") or "").strip()]).strip()
                if not merged_text:
                    continue

                print(f"[AIBrain] Processing {len(batch_messages)} messages for {queue_key}")
                await self.invoke_agent(event_id, {user_id: merged_text})

        except Exception as e:
            print(f"[AIBrain] Error in process_loop for {queue_key}: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if queue_key in self.processing_tasks:
                del self.processing_tasks[queue_key]
            if queue_key in self.message_queues and not self.message_queues[queue_key]:
                del self.message_queues[queue_key]
            print(f"[AIBrain] Process loop finished for {queue_key}")

    async def invoke_agent(self, event_id: str, user_inputs: dict):
        """
        Invokes the LangGraph agent with the constructed state.
        Now includes locking and internal state fetching to prevent race conditions.
        
        Args:
            event_id: The event ID string.
            user_inputs: Dictionary of new user inputs (id -> text).
            
        Returns:
            The final state (or None if execution finished).
        """
        if not hasattr(self, 'locks'):
            self.locks = {}

        lock_key = str(event_id)
        if isinstance(user_inputs, dict) and len(user_inputs) == 1:
            only_uid = next(iter(user_inputs.keys()))
            lock_key = f"{event_id}:{only_uid}"

        if lock_key not in self.locks:
            self.locks[lock_key] = asyncio.Lock()

        lock = self.locks[lock_key]
        
        async with lock:
            print(f"[DEBUG LOG] AIBrain.invoke_agent called for event {event_id} (Lock Acquired)")
            
            if not self.graph:
                print("[DEBUG LOG] Graph not initialized!")
                return None

            # 1. Fetch fresh state from DB
            db_cog = self.bot.get_cog("Database")
            if not db_cog:
                print("[ERROR] Database Cog not found within AIBrain")
                return None

            from bson import ObjectId
            if not ObjectId.is_valid(event_id):
                 print(f"[ERROR] Invalid Event ID: {event_id}")
                 return None

            event = await db_cog.get_event(ObjectId(event_id))
            if not event:
                 print(f"[ERROR] Event {event_id} not found during agent invoke.")
                 return None

            if event.get("cancelled") or event.get("workflow_state") in {"CANCELLED", "FAILED_MIN_PARTICIPANTS"}:
                print(f"[DEBUG LOG] Event {event_id} is cancelled/failed; skipping graph invoke.")
                return None

            # Construct Participants Dict
            participants = {}
            for p in event.get("participants", []):
                uid = p["user_id"]
                # Try to get user details
                user = self.bot.get_user(uid)
                if not user:
                    try:
                        user = await self.bot.fetch_user(uid)
                    except:
                        pass
                
                name = user.display_name if user else f"User{uid}"
                participants[uid] = {
                    "id": uid,
                    "name": name,
                    "answers": (p.get("interview", {}) or {}).get("answers", {}),
                    "status": p.get("status"),
                    "warning_count": p.get("warning_count", 0),
                    "history": p.get("conversation_history", [])
                }

            initial_state = {
                "event_id": event_id,
                "participants": participants,
                "messages": [], 
                "user_inputs": user_inputs
            }
            
            # Move invoke inside lock to ensure we don't start next one until this finishes?
            # YES. The whole point is to serialize the AI processing to prevent concurrent history updates.
            # If we release lock before invoke completes, the next request might fetch STALE history 
            # (because the first invoke hasn't written its response yet).
            # Writing response happens via TOOLS (send_message -> update_history).
            
            try:
                print(f"[DEBUG LOG] Starting graph.ainvoke with inputs: {user_inputs}")
                trace_config = {
                    "run_name": "jio_ba_interview_graph",
                    "tags": ["jio-ba", "discord", f"event:{event_id}"],
                    "metadata": {
                        "event_id": event_id,
                        "participant_count": len(participants),
                        "input_user_ids": list(user_inputs.keys()),
                    },
                }
                final_state = await self.graph.ainvoke(initial_state, config=trace_config)
                print("[DEBUG LOG] Graph ainvoke returned.")
                
                messages = final_state.get("messages", [])
                if messages:
                    last_msg = messages[-1]
                    print(f"[DEBUG LOG] Final Message: {last_msg}")
                    
                    if not last_msg.tool_calls and last_msg.content:
                        content = last_msg.content.strip()
                        if content in ["WAITING_FOR_USER", "WAITING_FOR_REPLY"]:
                            print(f"[DEBUG LOG] Internal Stop Signal Received: {content}")
                        else:
                            print("[DEBUG LOG] AI returned plain text. Sending as DM fallback.")
                            if user_inputs:
                                target_uid = next(iter(user_inputs))
                                user = self.bot.get_user(target_uid)
                                if user:
                                    try:
                                        await user.send(last_msg.content)
                                        # Fallback also needs persistence!
                                        await db_cog.append_history(ObjectId(event_id), target_uid, "model", last_msg.content)
                                        print(f"[DEBUG LOG] Sent fallback DM to {user.name}")
                                    except Exception as send_err:
                                        print(f"[DEBUG LOG] Failed to send fallback DM: {send_err}")
                                else:
                                     print(f"[DEBUG LOG] Target user {target_uid} not found for fallback DM.")
                            else:
                                print("[DEBUG LOG] No user_inputs found to target fallback DM.")
                return final_state
            except Exception as e:
                print(f"[DEBUG LOG] Graph Execution Error: {e}")
                import traceback
                traceback.print_exc()
                return None

def setup(bot):
    bot.add_cog(AIBrain(bot))
