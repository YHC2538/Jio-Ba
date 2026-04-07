import os
import json
import asyncio
from discord.ext import commands
# Import the graph creator
from .graph_agent import create_graph


def _message_content_to_text(content) -> str:
    """Normalize LangChain message content that may be str/list/dict into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        for key in ("text", "output_text", "content"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
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
    return str(content).strip()


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
        # Initialize the interview graph
        self.graph = create_graph()
        
        # Per-user processing state.
        # queue_key format: "{event_id}:{user_id}"
        self.message_queues = {} # queue_key -> list of {"content": msg, "name": name}
        self.processing_tasks = {} # queue_key -> asyncio.Task
        self.locks = {} # lock_key -> asyncio.Lock (event_id:user_id or event_id)
        self.thread_versions = {} # base_thread_key -> version int
        
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
                print(f"[AIBrain] Waiting 0.5s for debounce ({queue_key})...")
                await asyncio.sleep(0.5)

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

            # Determine Target Participant First
            target_uid = next(iter(user_inputs.keys())) if user_inputs else None

            # Fetch Participant Data
            target_participant = None
            if target_uid:
                target_participant = await db_cog.get_participant(ObjectId(event_id), int(target_uid))

            interview_data = (target_participant or {}).get("interview", {}) or {}
            user_profile = {}
            user_profile_hint = ""
            if target_uid:
                try:
                    user_profile = await db_cog.get_user_profile(int(target_uid))
                    user_profile_hint = await db_cog.get_user_profile_hint(int(target_uid), max_items_per_bucket=3)
                except Exception as profile_err:
                    print(f"[DEBUG LOG] Failed to hydrate user profile for {target_uid}: {profile_err}")
            
            # Convert user input to HumanMessage
            from langchain_core.messages import HumanMessage
            messages = []
            if target_uid and user_inputs.get(target_uid):
                messages.append(HumanMessage(content=user_inputs[target_uid]))

            initial_state = {
                "event_id": event_id,
                "user_id": str(target_uid) if target_uid else "",
                "messages": messages,
                "questions": event.get("interview_questions", []) or [],
                "dealbreakers": (target_participant or {}).get("dealbreakers", []) or [],
                "answers": interview_data.get("answers", {}) or {},
                "current_question_id": interview_data.get("current_question_id", ""),
                "warning_count": (target_participant or {}).get("warning_count", 0),
                "warning_threshold": int(((event.get("warning_policy", {}) or {}).get("threshold", 5))),
                "user_profile": user_profile,
                "user_profile_hint": user_profile_hint,
            }

            base_thread_key = f"{event_id}:{target_uid}" if target_uid is not None else str(event_id)
            version = int(self.thread_versions.get(base_thread_key, 1) or 1)
            thread_id = f"{base_thread_key}:v{version}"
            
            try:
                print(f"[DEBUG LOG] Starting graph.ainvoke with inputs: {user_inputs}")
                loading_msg = None
                entered_confirm_stage = False
                if target_uid:
                    try:
                        user_for_loading = self.bot.get_user(int(target_uid)) or await self.bot.fetch_user(int(target_uid))
                    except Exception as try_e:
                        print(f"[DEBUG LOG] Could not fetch user_for_loading: {try_e}")
                        user_for_loading = None
                    if user_for_loading:
                        try:
                            loading_msg = await user_for_loading.send("⏳ 正在思考與處理您的回覆中，請稍候...")
                        except Exception as e:
                            print(f"[DEBUG LOG] Failed to send loading message: {e}")

                input_user_names = {}
                for input_uid in list((user_inputs or {}).keys()):
                    display_name = None
                    if target_uid is not None and int(input_uid) == int(target_uid) and user_for_loading:
                        display_name = getattr(user_for_loading, "display_name", None) or getattr(user_for_loading, "name", None)
                    if not display_name:
                        try:
                            user_obj = self.bot.get_user(int(input_uid)) or await self.bot.fetch_user(int(input_uid))
                            display_name = getattr(user_obj, "display_name", None) or getattr(user_obj, "name", None)
                        except Exception:
                            display_name = None
                    input_user_names[str(input_uid)] = display_name or f"User {input_uid}"

                trace_config = {
                    "run_name": "jio_ba_interview_graph",
                    "tags": ["jio-ba", "discord", f"event:{event_id}"],
                    "metadata": {
                        "event_id": event_id,
                        "participant_count": len(event.get("participants", [])),
                        "input_user_ids": list(user_inputs.keys()),
                        "input_user_names": input_user_names,
                    },
                    "configurable": {
                        "thread_id": thread_id,
                    },
                }
                final_state = await self.graph.ainvoke(initial_state, config=trace_config)
                print("[DEBUG LOG] Graph ainvoke returned.")

                try:
                    # Write back to Database! State Hydration dictates we must save after returning
                    db_cog = self.bot.get_cog("Database")
                    if target_uid and final_state:
                         _uid = int(target_uid)
                         from bson import ObjectId
                         _event_id_obj = ObjectId(event_id)
                         
                         ans = final_state.get("answers", {})
                         cqid = final_state.get("current_question_id", "")
                         completed = final_state.get("interview_completed", False)
                         entered_confirm_stage = str(cqid or "") == "confirm_submit" and not bool(completed)

                         await db_cog.update_participant_interview(
                             _event_id_obj, _uid,
                             answers=ans,
                             current_question_id=cqid,
                             interview_completed=completed
                         )

                         if completed:
                             await db_cog.update_participant_status(_event_id_obj, _uid, "READY")
                             jio_cog_for_profile = self.bot.get_cog("Jio")
                             if jio_cog_for_profile:
                                 try:
                                     jio_cog_for_profile.trigger_profile_update_background(
                                         _event_id_obj,
                                         _uid,
                                         reason="graph_completed",
                                     )
                                 except Exception as profile_trigger_err:
                                     print(f"[DEBUG LOG] Failed to trigger profile update from graph completion: {profile_trigger_err}")
                             
                         # Check warnings
                         old_warnings = initial_state.get("warning_count", 0)
                         new_warnings = final_state.get("warning_count", 0)
                         if new_warnings > old_warnings:
                             reason = final_state.get("malicious_reason") or final_state.get("extracted", {}).get("malicious_reason", "AI 判定異常回覆")
                             
                             # Identify the exact question string and user reply
                             questions = initial_state.get("questions", [])
                             c_qid = initial_state.get("current_question_id", "")
                             q_text = ""
                             for q in questions:
                                 if str(q.get("id")) == str(c_qid):
                                     q_text = str(q.get("text", ""))
                             
                             reply_text = user_inputs.get(target_uid, "")
                             
                             context_dict = {
                                 "question": q_text,
                                 "reply": reply_text,
                                 "participant_reason": reason
                             }
                             
                             diff = new_warnings - old_warnings
                             for _ in range(diff):
                                 await db_cog.increment_participant_warning(_event_id_obj, _uid, reason, context=context_dict)
                             
                         if final_state.get("route") == "hold":
                             await db_cog.update_participant_status(_event_id_obj, _uid, "ON_HOLD")

                except Exception as db_err:
                     print(f"[ERROR] Failed to write state back to DB: {db_err}")
                     import traceback
                     traceback.print_exc()

                # Re-check adjudication after state updates from graph execution.
                jio_cog = self.bot.get_cog("Jio")
                if jio_cog:
                    try:
                        from bson import ObjectId
                        await jio_cog.maybe_trigger_adjudication(ObjectId(event_id) if ObjectId.is_valid(event_id) else event_id)
                    except Exception as adjudication_err:
                        print(f"[DEBUG LOG] maybe_trigger_adjudication failed after graph run: {adjudication_err}")

                if entered_confirm_stage and target_uid and jio_cog:
                    try:
                        user = self.bot.get_user(int(target_uid))
                        if not user:
                            user = await self.bot.fetch_user(int(target_uid))

                        sent_msg = await jio_cog.send_submission_review(
                            ObjectId(event_id) if ObjectId.is_valid(event_id) else event_id,
                            int(target_uid),
                            user=user,
                            message_to_edit=loading_msg,
                            event=event,
                        )

                        if not sent_msg and loading_msg:
                            await loading_msg.edit(content="🧾 請確認你的最終回答（系統正在重試建立確認卡片）")

                        await db_cog.append_history(
                            ObjectId(event_id),
                            target_uid,
                            "model",
                            "Entered final review stage. Waiting for participant confirm/edit via submission card.",
                            targets=[target_uid],
                            question_id=cqid,
                            message_type="system_event",
                        )
                    except Exception as review_err:
                        print(f"[DEBUG LOG] Failed to send submission review card: {review_err}")
                    return final_state
                
                messages = final_state.get("messages", [])
                if messages:
                    # We look for the last AIMessage
                    for msg in reversed(messages):
                        if msg.type == "ai":
                            content = _message_content_to_text(getattr(msg, "content", ""))
                            if content:
                                print(f"[DEBUG LOG] Graph response text: {content}")
                                if target_uid:
                                    try:
                                        user = self.bot.get_user(int(target_uid)) or await self.bot.fetch_user(int(target_uid))
                                    except Exception:
                                        user = None
                                    if user:
                                         try:
                                              import discord
                                              import json
                                              embed = None
                                              text_to_send = content
                                              log_content = content
                                              remain_tip = "\n\n⏳ 面試剩餘時間：未設定"

                                              jio_cog = self.bot.get_cog("Jio")
                                              if jio_cog:
                                                  remain = jio_cog._remaining_interview_minutes(event or {})
                                                  if remain is not None:
                                                      remain_tip = f"\n\n⏳ 面試剩餘時間：約 {remain} 分鐘"
                                              
                                              if content.startswith("EMBED_JSON:"):
                                                  try:
                                                      data = json.loads(content[11:])
                                                      embed_desc = str(data.get("description", "") or "")
                                                      if remain_tip:
                                                          embed_desc = f"{embed_desc}{remain_tip}" if embed_desc else remain_tip.strip()
                                                      embed = discord.Embed(
                                                          title=data.get("title", ""),
                                                          description=embed_desc,
                                                          color=data.get("color", 0x3498db)
                                                      )
                                                      text_to_send = None
                                                      log_content = embed_desc
                                                  except Exception as embed_err:
                                                      print(f"[DEBUG LOG] Failed to parse embed JSON: {embed_err}")
                                                      text_to_send = content.replace("EMBED_JSON:", "")
                                                      if remain_tip:
                                                          text_to_send = f"{text_to_send}{remain_tip}"
                                                      log_content = text_to_send
                                              elif remain_tip:
                                                  text_to_send = f"{text_to_send}{remain_tip}"
                                                  log_content = text_to_send
                                                      
                                              if loading_msg:
                                                  if embed:
                                                      await loading_msg.edit(content=None, embed=embed)
                                                  else:
                                                      await loading_msg.edit(content=text_to_send)
                                              else:
                                                  if embed:
                                                      await user.send(embed=embed)
                                                  else:
                                                      await user.send(text_to_send)
                                                      
                                              await db_cog.append_history(
                                                  ObjectId(event_id),
                                                  target_uid,
                                                  "model",
                                                  log_content,
                                                  targets=[target_uid],
                                                  question_id=final_state.get("current_question_id"),
                                                  message_type="ai_response",
                                              )
                                         except Exception as e:
                                              print(f"Failed to send DM: {e}")
                            break
                return final_state
            except Exception as e:
                print(f"[DEBUG LOG] Graph Execution Error: {e}")
                import traceback
                traceback.print_exc()
                return None

def setup(bot):
    bot.add_cog(AIBrain(bot))
