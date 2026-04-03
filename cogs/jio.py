import discord
from discord.ext import commands
from discord.ui import View, Button
import asyncio
import json
import os
import datetime
import re
from pymongo.errors import PyMongoError
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

from bson import ObjectId
from cogs.matching.nsw_calculator import CandidatePlan, UserProfile, rank_candidates


CORE_TOPICS = ["what", "where", "when", "how"]


def _clean_seed_dict(seed_dict) -> dict:
    cleaned = {}
    for key in CORE_TOPICS:
        value = str((seed_dict or {}).get(key) or "").strip()
        if value:
            cleaned[key] = value
    return cleaned


def _build_interview_questions_from_llm(seed_dict, generated_questions=None, custom_questions=None) -> list:
    generated_questions = generated_questions or {}
    custom_questions = custom_questions or []

    missing_topics = [topic for topic in CORE_TOPICS if not str((seed_dict or {}).get(topic) or "").strip()]
    questions = []

    for topic in missing_topics:
        candidate = str((generated_questions or {}).get(topic) or "").strip()
        if not candidate:
            raise ValueError(f"LLM response missing generated question for topic: {topic}")
        questions.append(
            {
                "id": f"core_{topic}",
                "topic": topic,
                "text": candidate,
                "required": True,
            }
        )

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


def _normalize_model_content(raw_content) -> str:
    """Normalize provider-specific content blocks into a plain text payload."""
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
        parts = []
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


def _is_system_instruction_not_supported(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return (
        "developer instruction" in msg
        or "system instruction" in msg
        or ("system" in msg and "not enabled" in msg)
    )


async def _ainvoke_with_system_fallback(llm, system_prompt: str, user_prompt: str, config=None):
    msgs = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    try:
        if config is not None:
            return await llm.ainvoke(msgs, config=config)
        return await llm.ainvoke(msgs)
    except Exception as exc:
        if not _is_system_instruction_not_supported(exc):
            raise

        fallback = HumanMessage(content=f"{system_prompt}\n\n---\n{user_prompt}")
        if config is not None:
            return await llm.ainvoke([fallback], config=config)
        return await llm.ainvoke([fallback])


def _safe_json_parse(raw_text: str):
    text = _normalize_model_content(raw_text)
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    if not text:
        return {}

    try:
        return json.loads(text)
    except Exception:
        # Recover when model wraps JSON with extra prose.
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return {}
        return {}


def _parse_llm_activity_payload(parsed_payload: dict):
    if not isinstance(parsed_payload, dict):
        raise ValueError("LLM response is not a JSON object")

    if "brief" not in parsed_payload:
        raise ValueError("LLM response missing brief")

    seeds_raw = parsed_payload.get("seeds")
    generated_raw = parsed_payload.get("generated_questions")

    if not isinstance(seeds_raw, dict):
        raise ValueError("LLM response missing seeds object")
    if not isinstance(generated_raw, dict):
        raise ValueError("LLM response missing generated_questions object")

    for key in CORE_TOPICS:
        if key not in seeds_raw:
            raise ValueError(f"LLM response missing seeds.{key}")
        if key not in generated_raw:
            raise ValueError(f"LLM response missing generated_questions.{key}")

    brief = str(parsed_payload.get("brief") or "").strip()
    seeds = _clean_seed_dict(seeds_raw)

    generated = {}
    for key in CORE_TOPICS:
        generated[key] = str(generated_raw.get(key) or "").strip()

    return brief, seeds, generated


async def parse_activity_and_questions_with_llm(text: str, title: str = "", custom_questions=None):
    raw = str(text or "").strip()
    custom_questions = custom_questions or []
    if not raw and not str(title or "").strip():
        raise ValueError("Activity input is empty")

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("[DEBUG][LLM] GOOGLE_API_KEY is missing. Strict LLM mode aborts activity parsing.")
        raise RuntimeError("LLM API is not available")

    try:
        llm = ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash"),
            google_api_key=api_key,
            temperature=0.15,
        )

        system_prompt = """
你是活動資料抽取與訪談題目設計助手。
請對使用者輸入做兩件事：
1) 抽取活動摘要與 seeds（what/where/when/how）。
2) 只針對尚未確定的 topic 產生訪談題目。

只輸出 JSON：
{{
  "brief": "",
  "seeds": {{
    "what": "",
    "where": "",
    "when": "",
    "how": ""
  }},
  "generated_questions": {{
    "what": "",
    "where": "",
    "when": "",
    "how": ""
  }}
}}

規則：
1) 若某欄無法判斷，留空字串。
2) brief 應是 1~2 句精簡摘要。
3) generated_questions 只能填入「缺失 topic」的題目；已經有 seeds 的 topic 保持空字串。
4) 題目需為繁體中文、可直接回答、具體，不要空泛。
5) 禁止輸出 why 欄位與 why 題。
6) 如果使用者在活動描述中，可能無明確決定 seeds，然而針對 seeds 或問題設計有條件限制（例如「只能在台北」、「只能晚上」、「時間必須精確到幾時幾分」），請務必根據條件限制來設計訪談題目。(但仍然必須按照JSON格式輸出，不要在 JSON 以外的地方說明)。
7) 不要輸出多餘文字。
"""
        user_prompt = (
            f"活動標題: {str(title or '').strip()}\n"
            f"User 輸入活動描述: {raw}\n"
            f"自訂問題（最多1題）: {json.dumps([str(q).strip() for q in custom_questions[:1] if str(q).strip()], ensure_ascii=False)}"
        )
        trace_config = {
            "run_name": "parse_activity_and_questions",
            "tags": ["jio-ba", "discord", "event_creation"], # 貼上標籤方便過濾
        }

        resp = await _ainvoke_with_system_fallback(
            llm,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            config=trace_config,
        )
        parsed = _safe_json_parse(getattr(resp, "content", ""))

        brief, seeds, generated_question_map = _parse_llm_activity_payload(parsed)
        interview_questions = _build_interview_questions_from_llm(
            seeds,
            generated_questions=generated_question_map,
            custom_questions=custom_questions,
        )
        final_brief = brief or raw
        return final_brief, seeds, interview_questions
    except Exception as parse_err:
        print(f"[DEBUG][LLM] parse_activity_and_questions_with_llm failed: {parse_err}")
        raise


def format_activity_seeds(seeds: dict) -> str:
    items = []
    labels = {
        "what": "What",
        "where": "Where",
        "when": "When",
        "how": "How",
    }
    for key in CORE_TOPICS:
        value = str((seeds or {}).get(key) or "").strip()
        if value:
            items.append(f"{labels[key]}: {value}")
    return "\n".join(items)


def build_optional_input_text(**kwargs):
    """
    Workaround for py-cord 2.7.x where required=False may serialize as required=None.
    Force component payload required=False at underlying layer.
    """
    comp = discord.ui.InputText(required=False, **kwargs)
    try:
        comp._underlying.required = False
    except Exception:
        pass
    return comp

class JioCreationModal(discord.ui.Modal):
    def __init__(
        self,
        bot,
        chan_id,
        default_title=None,
        default_signup_time=None,
        default_interview_time=None,
        default_min_participants=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.channel_id = chan_id
        self.default_min_participants = default_min_participants
        
        self.add_item(discord.ui.InputText(
            label="活動標題",
            placeholder="例如：週末桌遊、台北半日遊、讀書會",
            style=discord.InputTextStyle.short,
            required=True,
            value=default_title
        ))
        
        self.add_item(build_optional_input_text(
            label="活動資訊 / 4W seeds",
            placeholder="請自由描述活動，建議可提到 what/where/when/how 關鍵資訊",
            style=discord.InputTextStyle.long,
            min_length=0,
        ))
        
        self.add_item(build_optional_input_text(
            label="報名截止時間（分鐘，選填）",
            placeholder="例如：30",
            style=discord.InputTextStyle.short,
            min_length=0,
            value=str(default_signup_time) if default_signup_time else None,
        ))

        self.add_item(build_optional_input_text(
            label="面試截止時間（分鐘，選填）",
            placeholder="例如：90",
            style=discord.InputTextStyle.short,
            min_length=0,
            value=str(default_interview_time) if default_interview_time else None,
        ))

        self.add_item(build_optional_input_text(
            label="最低成團人數（選填）",
            placeholder="例如：4",
            style=discord.InputTextStyle.short,
            min_length=0,
            value=str(default_min_participants) if default_min_participants else None,
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        title = self.children[0].value
        description = self.children[1].value
        signup_input = self.children[2].value
        interview_input = self.children[3].value
        min_input = self.children[4].value
        
        # Validations
        signup_limit = None
        if str(signup_input or "").strip():
            try:
                signup_limit = int(signup_input)
            except ValueError:
                await interaction.followup.send("❌ 報名截止時間必須是數字。", ephemeral=True)
                return

        interview_limit = None
        if str(interview_input or "").strip():
            try:
                interview_limit = int(interview_input)
            except ValueError:
                await interaction.followup.send("❌ 面試截止時間必須是數字。", ephemeral=True)
                return

        min_participants = None
        if str(min_input or "").strip():
            try:
                min_participants = int(min_input)
            except ValueError:
                await interaction.followup.send("❌ 最低成團人數必須是數字。", ephemeral=True)
                return

        loading_msg = await interaction.followup.send(
            embed=discord.Embed(
                title="⏳ 正在建立 Jio 活動",
                description="正在準備主畫面，請稍候...",
                color=0x3498DB,
            )
        )

        spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spinner_alive = True

        async def _spinner():
            idx = 0
            while spinner_alive:
                dots = "." * ((idx % 3) + 1)
                frame = spinner_frames[idx % len(spinner_frames)]
                try:
                    await loading_msg.edit(
                        embed=discord.Embed(
                            title=f"{frame} 正在建立 Jio 活動",
                            description=f"正在準備主畫面{dots}",
                            color=0x3498DB,
                        )
                    )
                except Exception:
                    pass
                idx += 1
                await asyncio.sleep(0.8)

        spinner_task = asyncio.create_task(_spinner())

        try:
            parsed_brief, activity_seeds, prebuilt_questions = await parse_activity_and_questions_with_llm(
                description,
                title=title,
                custom_questions=[],
            )
        except Exception as llm_err:
            print(f"[DEBUG][LLM] Activity creation aborted: {llm_err}")
            spinner_alive = False
            spinner_task.cancel()
            await interaction.followup.send(
                "❌ 活動建立失敗：目前無法使用 LLM 解析活動資訊，請稍後再試。",
                ephemeral=True,
            )
            return

        # Prepare description
        final_description = parsed_brief if parsed_brief else "（由參與者訪談共同完善）"
        custom_questions = []

        now = datetime.datetime.utcnow()
        signup_deadline = now + datetime.timedelta(minutes=signup_limit) if signup_limit else None
        interview_deadline = now + datetime.timedelta(minutes=interview_limit) if interview_limit else None

        db = self.bot.get_cog("Database")
        if not db:
            spinner_alive = False
            spinner_task.cancel()
            await interaction.followup.send("❌ 系統錯誤：找不到資料庫模組，請稍後再試。", ephemeral=True)
            return
        
        # 1. Create event
        try:
            event_id = await db.create_event(
                initiator_id=interaction.user.id,
                description=final_description,
                channel_id=self.channel_id,
                initiator_name=interaction.user.display_name,
                title=title,
                signup_deadline=signup_deadline,
                interview_deadline=interview_deadline,
                min_participants=min_participants if min_participants is not None else self.default_min_participants,
                activity_seeds=activity_seeds,
                custom_questions=custom_questions,
                prebuilt_questions=prebuilt_questions,
            )
        except PyMongoError as exc:
            print(f"[JioCreationModal] MongoDB error while creating event: {exc}")
            spinner_alive = False
            spinner_task.cancel()
            await interaction.followup.send(
                "❌ 目前無法連線到資料庫，活動建立失敗。請確認 `MONGO_URI` 是否正確，或稍後再試。",
                ephemeral=True,
            )
            return
        except Exception as exc:
            print(f"[JioCreationModal] Unexpected error while creating event: {exc}")
            spinner_alive = False
            spinner_task.cancel()
            await interaction.followup.send("❌ 建立活動時發生未知錯誤，請稍後再試。", ephemeral=True)
            return
        
        # 2. Prepare Embed
        embed_title = f"🎯 活動: {title}"
        embed_desc = f"{final_description}\n\n點擊下方按鈕加入！"

        if signup_deadline:
            ts = int(signup_deadline.replace(tzinfo=datetime.timezone.utc).timestamp())
            embed_desc += f"\n\n⏳ **報名截止**: <t:{ts}:R>"
        if interview_deadline:
            ts2 = int(interview_deadline.replace(tzinfo=datetime.timezone.utc).timestamp())
            embed_desc += f"\n🕒 **面試截止**: <t:{ts2}:R>"

        seed_text = format_activity_seeds(activity_seeds)
        if seed_text:
            embed_desc += f"\n\n**4W Seeds**\n{seed_text}"

        effective_min = min_participants if min_participants is not None else self.default_min_participants
        if effective_min:
            embed_desc += f"\n\n👥 **最低成團人數**: {int(effective_min)}"
        
        embed = discord.Embed(title=embed_title, description=embed_desc, color=0x00ff00)
        embed.set_footer(text=f"發起人: {interaction.user.display_name}")
        
        view = JoinView(self.bot, event_id)

        spinner_alive = False
        spinner_task.cancel()

        # 3. Replace Loading Message with Main Card
        try:
            await loading_msg.edit(embed=embed, view=view)
            await db.update_event_message_id(event_id, loading_msg.id)
        except Exception:
            msg = await interaction.followup.send(embed=embed, view=view)
            await db.update_event_message_id(event_id, msg.id)
        
        # Handle signup close timer
        if signup_limit:
            jio_cog = self.bot.get_cog("Jio")
            if jio_cog:
                self.bot.loop.create_task(jio_cog.close_event_after(event_id, signup_limit * 60))



class JioEditModal(discord.ui.Modal):
    def __init__(self, bot, event_id, current_title, current_desc, current_time, current_custom="", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.event_id = event_id
        
        self.add_item(discord.ui.InputText(
            label="活動標題",
            value=current_title,
            style=discord.InputTextStyle.short,
            required=True
        ))
        
        self.add_item(build_optional_input_text(
            label="活動說明",
            value=current_desc,
            style=discord.InputTextStyle.long,
            min_length=0,
        ))
        
        self.add_item(build_optional_input_text(
            label="報名截止時間(分鐘，選填)",
            value=str(current_time) if current_time else "",
            placeholder="留空則不變更",
            style=discord.InputTextStyle.short,
            min_length=0,
        ))

        self.add_item(build_optional_input_text(
            label="面試截止時間(分鐘，選填)",
            value="",
            placeholder="留空則不變更",
            style=discord.InputTextStyle.short,
            min_length=0,
        ))

        self.add_item(build_optional_input_text(
            label="自訂問題（選填，最多1題）",
            value=str(current_custom or ""),
            placeholder="留空代表移除自訂問題",
            style=discord.InputTextStyle.long,
            min_length=0,
        ))
        
    async def callback(self, interaction: discord.Interaction):
        deferred = False
        try:
            await interaction.response.defer(ephemeral=True)
            deferred = True
        except discord.errors.InteractionResponded:
            deferred = True
        except Exception:
            deferred = False

        async def _reply(text: str):
            if deferred:
                await interaction.followup.send(text, ephemeral=True)
                return
            try:
                await interaction.response.send_message(text, ephemeral=True)
            except discord.errors.InteractionResponded:
                await interaction.followup.send(text, ephemeral=True)
        
        new_title = self.children[0].value
        new_desc = self.children[1].value
        signup_input = self.children[2].value
        interview_input = self.children[3].value
        custom_input = self.children[4].value
        custom_question = str(custom_input or "").strip()
        host_custom_questions = [custom_question] if custom_question else []
        
        db = self.bot.get_cog("Database")
        event = await db.get_event(self.event_id)
            
        update_data = {
            "title": new_title,
            "description": new_desc if new_desc else "（由大家討論決定）",
            "host_custom_questions": host_custom_questions,
        }

        title_changed = bool(event) and str(event.get("title") or "").strip() != str(new_title or "").strip()
        desc_changed = bool(event) and str(event.get("description") or "").strip() != str(update_data["description"] or "").strip()
        existing_custom = (event.get("host_custom_questions", []) if event else []) or []
        existing_custom_first = str(existing_custom[0] if existing_custom else "").strip()
        custom_changed = existing_custom_first != (host_custom_questions[0] if host_custom_questions else "")

        should_regenerate = bool(event) and (title_changed or desc_changed or custom_changed)
        unified_questions = None

        if should_regenerate:
            seed_input = f"標題: {new_title}\n說明: {update_data['description']}"
            try:
                parsed_brief, parsed_seeds, unified_questions = await parse_activity_and_questions_with_llm(
                    seed_input,
                    title=new_title,
                    custom_questions=host_custom_questions,
                )
            except Exception as llm_err:
                print(f"[DEBUG][LLM] Activity edit aborted: {llm_err}")
                await _reply("❌ 設定更新失敗：目前無法使用 LLM 解析活動資訊，請稍後再試。")
                return

            update_data["activity_seeds"] = {
                "what": str((parsed_seeds or {}).get("what") or "").strip() or None,
                "where": str((parsed_seeds or {}).get("where") or "").strip() or None,
                "when": str((parsed_seeds or {}).get("when") or "").strip() or None,
                "how": str((parsed_seeds or {}).get("how") or "").strip() or None,
            }

            if desc_changed and str(new_desc or "").strip() == "" and str(parsed_brief or "").strip():
                update_data["description"] = str(parsed_brief).strip()
        
        if str(signup_input or "").strip():
            try:
                minutes = int(str(signup_input).strip())
                new_deadline = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
                update_data["signup_deadline"] = new_deadline
            except ValueError:
                await _reply("❌ 報名截止時間格式錯誤，僅已更新其他設定。")

        if str(interview_input or "").strip():
            try:
                minutes = int(str(interview_input).strip())
                update_data["interview_deadline"] = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
            except ValueError:
                await _reply("❌ 面試截止時間格式錯誤，僅已更新其他設定。")

        if event and event.get("active", True):
            if should_regenerate:
                update_data["interview_questions"] = unified_questions
        
        await db.events.update_one({"_id": self.event_id}, {"$set": update_data})

        if event and event.get("active", True):
            first_qid = (update_data.get("interview_questions") or [{}])[0].get("id") if update_data.get("interview_questions") else None
            if first_qid:
                await db.events.update_one(
                    {"_id": self.event_id},
                    {"$set": {"participants.$[elem].interview.current_question_id": first_qid}},
                    array_filters=[{"elem.status": {"$in": ["PENDING", "INTERVIEWING"]}}],
                )

        if str(signup_input or "").strip():
            jio_cog = self.bot.get_cog("Jio")
            if jio_cog:
                self.bot.loop.create_task(jio_cog.close_event_after(self.event_id, int(str(signup_input).strip()) * 60))
        
        # Update Dashboard
        jio_cog = self.bot.get_cog("Jio")
        if jio_cog:
            await jio_cog.update_dashboard(self.event_id)
            
        await _reply("✅ 設定已更新！")


class ManageSelect(discord.ui.Select):
    def __init__(self, bot, event_id, include_progress=False):
        self.bot = bot
        self.event_id = event_id
        
        options = [
            discord.SelectOption(
                label="提早結束 (End Early)", 
                value="END", 
                description="[發起人] 立即截止報名並開始面試",
                emoji="⚡"
            ),
            discord.SelectOption(
                label="編輯設定 (Edit Settings)", 
                value="EDIT_SETTINGS", 
                description="[發起人] 修改標題、說明、時間與自訂問題",
                emoji="⚙️"
            ),
            discord.SelectOption(
                label="處理暫停名單 (Review ON_HOLD)",
                value="REVIEW_HOLD",
                description="[發起人] 直接在 UI 裁決暫停中的參與者",
                emoji="🧑‍⚖️"
            ),
            discord.SelectOption(
                label="取消活動 (Cancel Event)",
                value="CANCEL_EVENT",
                description="[發起人] 立即取消並停止所有訪談",
                emoji="🛑"
            ),
        ]

        if include_progress:
            options.insert(
                2,
                discord.SelectOption(
                    label="查看面試進度 (Interview Progress)",
                    value="VIEW_PROGRESS",
                    description="[發起人] 查看每位參與者目前面試進度",
                    emoji="📊"
                ),
            )
        
        super().__init__(
            placeholder="管理選單 (Management Menu)...",
            min_values=1,
            max_values=1,
            options=options,
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        db = self.bot.get_cog("Database")
        event = await db.get_event(self.event_id)
        
        if not event:
            await interaction.response.send_message("活動已不存在。", ephemeral=True)
            return

        user_id = interaction.user.id
        initiator_id = event.get("initiator_id")
        
        if value == "END":
            # Host Only
            if user_id != initiator_id:
                await interaction.response.send_message("⛔ 只有發起人可以執行此操作。", ephemeral=True)
                return

            interview_started = not event.get("active", True)
            view = EndEarlyChoiceView(self.bot, self.event_id, allow_end_signup=not interview_started)
            if interview_started:
                await interaction.response.send_message(
                    "面試已開始，無法再提早截止報名；你仍可提早結束面試。",
                    view=view,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message("請選擇要提早結束報名或提早結束面試。", view=view, ephemeral=True)
                
        elif value == "EDIT_SETTINGS":
             # Host Only
            if user_id != initiator_id:
                await interaction.response.send_message("⛔ 只有發起人可以編輯設定。", ephemeral=True)
                return
            
            # Show Edit Modal
            modal = JioEditModal(
                self.bot, 
                self.event_id,
                current_title=event.get("title", ""),
                current_desc=event.get("description", ""),
                current_time=None,
                current_custom=(event.get("host_custom_questions", [""]) or [""])[0],
                title="編輯活動設定"
            )
            await interaction.response.send_modal(modal)

        elif value == "VIEW_PROGRESS":
            if user_id != initiator_id:
                await interaction.response.send_message("⛔ 只有發起人可以查看面試進度。", ephemeral=True)
                return
            if event.get("active", True):
                await interaction.response.send_message("目前仍在報名階段，面試尚未開始。", ephemeral=True)
                return

            jio_cog = self.bot.get_cog("Jio")
            if jio_cog:
                embed = await jio_cog.build_interview_progress_embed(self.event_id)
                await interaction.response.send_message(embed=embed, ephemeral=True)

        elif value == "REVIEW_HOLD":
            if user_id != initiator_id:
                await interaction.response.send_message("⛔ 只有發起人可以裁決。", ephemeral=True)
                return

            jio_cog = self.bot.get_cog("Jio")
            if jio_cog:
                await jio_cog.open_hold_review_ui(self.event_id, interaction)

        elif value == "CANCEL_EVENT":
            if user_id != initiator_id:
                await interaction.response.send_message("⛔ 只有發起人可以取消活動。", ephemeral=True)
                return

            modal = CancelEventModal(self.bot, self.event_id, title="取消活動")
            await interaction.response.send_modal(modal)


class JoinView(View):
    def __init__(self, bot, event_id):
        super().__init__(timeout=None) # Persistent view
        self.bot = bot
        self.event_id = event_id
        self.add_item(ManageSelect(bot, event_id, include_progress=False))


    @discord.ui.button(label="✋ 我要參加 (Join)", style=discord.ButtonStyle.green, custom_id="join_btn")
    async def join_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        db = self.bot.get_cog("Database")
        event = await db.get_event(self.event_id)
        
        if not event:
            await interaction.response.send_message("活動已不存在。", ephemeral=True)
            return

        # Check Invite Only
        if not event.get("active", True):
             await interaction.response.send_message("⛔ 報名已截止，無法加入！", ephemeral=True)
             return

        # Check if already joined
        for p in event.get("participants", []):
            if p["user_id"] == interaction.user.id:
                 await interaction.response.send_message("✅ 您已經報名過了！", ephemeral=True)
                 return

        deferred = False
        try:
            await interaction.response.defer(ephemeral=True)
            deferred = True
        except discord.errors.InteractionResponded:
            deferred = True
        except Exception:
            deferred = False

        db = self.bot.get_cog("Database")

        await db.add_participant(
            self.event_id,
            interaction.user.id,
            user_name=interaction.user.display_name,
        )

        await db.append_history(
            self.event_id,
            interaction.user.id,
            "system",
            "Participant joined the activity.",
            author_name=interaction.user.display_name,
            targets=[interaction.user.id],
            message_type="system_event",
        )
        await db.update_participant_status(self.event_id, interaction.user.id, "PENDING")
        
        jio_cog = self.bot.get_cog("Jio")
        if jio_cog:
            await jio_cog.update_dashboard(self.event_id)
            await jio_cog.log_event_state(self.event_id)

        if deferred:
            await interaction.followup.send("✅ 已加入活動！\n⏳ 報名截止後，Bot 會私訊你開始訪談。", ephemeral=True)
        else:
            try:
                await interaction.response.send_message("✅ 已加入活動！\n⏳ 報名截止後，Bot 會私訊你開始訪談。", ephemeral=True)
            except discord.errors.InteractionResponded:
                await interaction.followup.send("✅ 已加入活動！\n⏳ 報名截止後，Bot 會私訊你開始訪談。", ephemeral=True)


class AdjudicationView(View):
    def __init__(self, bot, event_id):
        super().__init__(timeout=86400)
        self.bot = bot
        self.event_id = event_id

    async def _check_event_actionable(self, interaction: discord.Interaction) -> bool:
        db = self.bot.get_cog("Database")
        if not db:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return False

        event = await db.get_event(self.event_id)
        if not event:
            await interaction.response.send_message("活動已不存在，按鈕將停用。", ephemeral=True)
            return False

        closed = (
            event.get("cancelled")
            or event.get("workflow_state") in {"CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"}
            or event.get("adjudication_status") in {"DECIDED", "CANCELLED"}
        )
        if not closed:
            return True

        for child in self.children:
            child.disabled = True
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except Exception:
            pass

        try:
            await interaction.response.send_message("此活動已結束，按鈕已停用。", ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send("此活動已結束，按鈕已停用。", ephemeral=True)
        return False

    @discord.ui.button(label="採用方案 1", style=discord.ButtonStyle.green)
    async def choose_plan_1(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._check_event_actionable(interaction):
            return

        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        # removed defer because edit_message handles it

        await jio_cog.apply_adjudication_choice(self.event_id, interaction.user.id, 1, interaction)

    @discord.ui.button(label="採用方案 2", style=discord.ButtonStyle.blurple)
    async def choose_plan_2(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._check_event_actionable(interaction):
            return

        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        # removed defer because edit_message handles it

        await jio_cog.apply_adjudication_choice(self.event_id, interaction.user.id, 2, interaction)


class CancelEventModal(discord.ui.Modal):
    def __init__(self, bot, event_id, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.event_id = event_id

        self.add_item(build_optional_input_text(
            label="取消原因（選填）",
            placeholder="例如：人數不足、時程變更",
            style=discord.InputTextStyle.long,
            min_length=0,
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        reason = self.children[0].value
        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.followup.send("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        # removed defer because edit_message handles it
        ok = await jio_cog.cancel_event_with_announcement(
            self.event_id,
            interaction.user.id,
            reason=reason,
            source="HOST_UI",
        )
        if ok:
            await interaction.followup.send("✅ 活動已取消，訪談已停止。", ephemeral=True)
        else:
            await interaction.followup.send("❌ 取消失敗，請稍後再試。", ephemeral=True)


class HoldVerdictReasonModal(discord.ui.Modal):
    def __init__(
        self,
        bot,
        event_id,
        target_user_id,
        verdict,
        source_view=None,
        source_message=None,
        parent_view=None,
        parent_message=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.event_id = event_id
        self.target_user_id = target_user_id
        self.verdict = verdict
        self.source_view = source_view
        self.source_message = source_message
        self.parent_view = parent_view
        self.parent_message = parent_message

        self.add_item(discord.ui.InputText(
            label="裁決原因（必填）",
            placeholder="請填寫裁決原因",
            style=discord.InputTextStyle.long,
            required=True,
        ))

    async def callback(self, interaction: discord.Interaction):
        db = self.bot.get_cog("Database")
        if not db:
            await interaction.response.send_message("資料庫模組不可用", ephemeral=True)
            return

        # removed defer because edit_message handles it
        reason = self.children[0].value
        ok = await db.apply_host_verdict(self.event_id, self.target_user_id, self.verdict, reason=reason)
        if not ok:
            try:
                await interaction.response.send_message("❌ 裁決失敗", ephemeral=True)
            except discord.errors.InteractionResponded:
                await interaction.followup.send("❌ 裁決失敗", ephemeral=True)
            return

        target = self.bot.get_user(self.target_user_id)
        if not target:
            try:
                target = await self.bot.fetch_user(self.target_user_id)
            except Exception:
                target = None

        if target:
            try:
                if self.verdict == "KICK":
                    await db.remove_participating_event(self.target_user_id, str(self.event_id))
                    await target.send(f"🔨 主揪已裁決你離開活動。理由：{reason}")
                else:
                    event = await db.get_event(self.event_id)
                    participant = await db.get_participant(self.event_id, self.target_user_id)
                    await db.set_participating_event_status(self.target_user_id, str(self.event_id), "INTERVIEWING")
                    await db.set_focus_event(self.target_user_id, str(self.event_id), status="INTERVIEWING")

                    question_text = "請繼續回答上一題。"
                    if event and participant:
                        current_qid = str(((participant.get("interview", {}) or {}).get("current_question_id") or "").strip())
                        qmap = {str(q.get("id")): str(q.get("text") or "") for q in (event.get("interview_questions", []) or [])}
                        if current_qid and current_qid in qmap:
                            question_text = qmap[current_qid]

                    remain_tip = ""
                    jio_cog = self.bot.get_cog("Jio")
                    if jio_cog:
                        remain = jio_cog._remaining_interview_minutes(event or {})
                        if remain is not None:
                            remain_tip = f"\n⏳ 面試剩餘時間：約 {remain} 分鐘"

                    await target.send(
                        f"🔨 主揪已裁決你可繼續訪談。理由：{reason}\n"
                        f"請繼續上一題：{question_text}{remain_tip}"
                    )
            except Exception:
                pass

        jio_cog = self.bot.get_cog("Jio")
        if jio_cog:
            if self.verdict == "KICK":
                await jio_cog.promote_next_queued_event_for_user(self.target_user_id, completed_event_id=self.event_id)
            await jio_cog.update_dashboard(self.event_id)

        if self.source_view and self.source_message:
            for child in self.source_view.children:
                child.disabled = True
            try:
                await self.source_message.edit(view=self.source_view)
            except Exception:
                pass

        if self.parent_view and self.parent_message:
            for child in self.parent_view.children:
                child.disabled = True
            try:
                await self.parent_message.edit(view=self.parent_view)
            except Exception:
                pass

        try:
            if self.verdict == "KICK":
                await interaction.response.send_message("✅ 已移出該參與者。", ephemeral=True)
            else:
                await interaction.response.send_message("✅ 已裁決為可繼續訪談。", ephemeral=True)
        except discord.errors.InteractionResponded:
            if self.verdict == "KICK":
                await interaction.followup.send("✅ 已移出該參與者。", ephemeral=True)
            else:
                await interaction.followup.send("✅ 已裁決為可繼續訪談。", ephemeral=True)


class HoldVerdictActionView(View):
    def __init__(self, bot, event_id, target_user_id, parent_view=None, parent_message=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.event_id = event_id
        self.target_user_id = target_user_id
        self.parent_view = parent_view
        self.parent_message = parent_message

    @discord.ui.button(label="裁決繼續 (CONTINUE)", style=discord.ButtonStyle.green)
    async def continue_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = HoldVerdictReasonModal(
            self.bot,
            self.event_id,
            self.target_user_id,
            "CONTINUE",
            source_view=self,
            source_message=interaction.message,
            parent_view=self.parent_view,
            parent_message=self.parent_message,
            title="裁決為繼續",
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="裁決移出 (KICK)", style=discord.ButtonStyle.red)
    async def kick_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = HoldVerdictReasonModal(
            self.bot,
            self.event_id,
            self.target_user_id,
            "KICK",
            source_view=self,
            source_message=interaction.message,
            parent_view=self.parent_view,
            parent_message=self.parent_message,
            title="裁決為移出",
        )
        await interaction.response.send_modal(modal)


class HoldKickSelect(discord.ui.Select):
    def __init__(self, bot, event_id, candidates):
        self.bot = bot
        self.event_id = event_id
        options = []
        for p in candidates[:25]:
            uid = p.get("user_id")
            label = p.get("display_name") or p.get("name") or f"User {uid}"
            context = p.get("hold_context") or {}
            question = str(context.get("question") or "").strip()
            reply = str(context.get("reply") or "").strip()
            summary = ""
            if question and reply:
                summary = f"Q:{question[:35]} | A:{reply[:35]}"
            elif context.get("participant_reason"):
                summary = str(context.get("participant_reason"))[:70]
            else:
                summary = "點選後裁決 CONTINUE 或 KICK"
            options.append(discord.SelectOption(label=label[:100], value=str(uid), description=summary[:100]))

        super().__init__(
            placeholder="選擇要處理的成員",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        uid = int(self.values[0])
        db = self.bot.get_cog("Database")
        event = await db.get_event(self.event_id) if db else None
        participant = None
        if event:
            for p in event.get("participants", []):
                if p.get("user_id") == uid:
                    participant = p
                    break

        hold_context = (participant or {}).get("hold_context", {}) or {}
        q = str(hold_context.get("question") or "(無)")
        a = str(hold_context.get("reply") or "(無)")
        reason = str(hold_context.get("participant_reason") or hold_context.get("reason") or "(無)")

        view = HoldVerdictActionView(
            self.bot,
            self.event_id,
            uid,
            parent_view=self.view,
            parent_message=interaction.message,
        )
        msg = (
            f"請選擇裁決動作。\n"
            f"問題：{q}\n"
            f"參與者回覆：{a}\n"
            f"Bot判定：{reason}"
        )
        await interaction.response.send_message(msg, view=view, ephemeral=True)


class HoldKickView(View):
    def __init__(self, bot, event_id, candidates):
        super().__init__(timeout=600)
        self.add_item(HoldKickSelect(bot, event_id, candidates))


class ManageOnlyView(View):
    def __init__(self, bot, event_id):
        super().__init__(timeout=None)
        self.add_item(ManageSelect(bot, event_id, include_progress=True))


class DMEventSwitchSelect(discord.ui.Select):
    def __init__(self, bot, user_id, events):
        self.bot = bot
        self.user_id = user_id
        self.events = events
        options = []
        for idx, event in enumerate(events[:25], start=1):
            options.append(
                discord.SelectOption(
                    label=f"{idx}. {event.get('title', '未命名活動')}"[:100],
                    value=str(event.get("_id")),
                    description="切換目前訪談活動",
                )
            )
        super().__init__(placeholder="切換目前訪談活動", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        db = self.bot.get_cog("Database")
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的切換選單。", ephemeral=True)
            return

        selected_id = self.values[0]
        target_event = None
        for event in self.events:
            if str(event.get("_id")) == selected_id:
                target_event = event
                break

        if not target_event:
            await interaction.response.send_message("找不到該活動，請重新整理。", ephemeral=True)
            return

        await db.set_focus_event(self.user_id, str(target_event.get("_id")), status="INTERVIEWING")
        jio_cog = self.bot.get_cog("Jio")
        detail = await jio_cog.describe_current_interview_state(target_event, self.user_id) if jio_cog else ""
        await interaction.response.send_message(f"✅ 已切換到活動：{target_event.get('title', '未命名活動')}\n\n{detail}")


class DMEventSwitchView(View):
    def __init__(self, bot, user_id, events):
        super().__init__(timeout=600)
        self.add_item(DMEventSwitchSelect(bot, user_id, events))


class EndEarlyChoiceView(View):
    def __init__(self, bot, event_id, allow_end_signup=True):
        super().__init__(timeout=180)
        self.bot = bot
        self.event_id = event_id

        if not allow_end_signup:
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.label == "提早結束報名":
                    child.disabled = True

    @discord.ui.button(label="提早結束報名", style=discord.ButtonStyle.blurple)
    async def end_signup(self, button: discord.ui.Button, interaction: discord.Interaction):
        db = self.bot.get_cog("Database")
        event = await db.get_event(self.event_id) if db else None
        if not event:
            await interaction.response.send_message("活動已不存在。", ephemeral=True)
            return

        if not event.get("active", True):
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("⛔ 面試已開始，無法再提早截止報名。請改用「提早結束面試」。", ephemeral=True)
            return

        # Disable buttons immediately to prevent double clicks
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        
        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        # removed defer because edit_message handles it
        started = await jio_cog.start_interview_phase(self.event_id, manual_trigger_user=interaction.user)
        if started:
            await interaction.followup.send("✅ 已提前截止報名並開始面試。", ephemeral=True)
            return

        await interaction.followup.send("ℹ️ 此活動已在面試流程中，無需再次提早截止報名。", ephemeral=True)

    @discord.ui.button(label="提早結束面試", style=discord.ButtonStyle.red)
    async def end_interview(self, button: discord.ui.Button, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        
        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        # removed defer because edit_message handles it
        await jio_cog.end_interview_early(self.event_id, trigger_user=interaction.user)
        await interaction.followup.send("✅ 已提前結束面試流程。", ephemeral=True)


class ConfirmEditQuestionSelect(discord.ui.Select):
    def __init__(self, bot, event_id, user_id, options):
        self.bot = bot
        self.event_id = event_id
        self.user_id = user_id
        super().__init__(
            placeholder="選擇要手動修改答案的題目",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的修改選單。", ephemeral=True)
            return

        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        await jio_cog.open_manual_edit_modal(
            self.event_id,
            self.user_id,
            self.values[0],
            interaction,
            source_view=self.view,
            source_message=interaction.message,
        )


class ManualAnswerEditModal(discord.ui.Modal):
    def __init__(
        self,
        bot,
        event_id,
        user_id,
        question_id,
        question_text,
        current_answer,
        source_view=None,
        source_message=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.event_id = event_id
        self.user_id = user_id
        self.question_id = question_id
        self.question_text = str(question_text or "")
        self.source_view = source_view
        self.source_message = source_message

        self.add_item(build_optional_input_text(
            label="問題（僅供參考）",
            value=self.question_text[:4000],
            style=discord.InputTextStyle.long,
            min_length=0,
        ))
        self.add_item(discord.ui.InputText(
            label="請填寫新的答案",
            value=str(current_answer or "")[:4000],
            placeholder="直接手動輸入你的最終答案",
            style=discord.InputTextStyle.long,
            required=True,
        ))

    async def callback(self, interaction: discord.Interaction):
        deferred = False
        try:
            await interaction.response.defer(ephemeral=True)
            deferred = True
        except discord.errors.InteractionResponded:
            deferred = True
        except Exception:
            deferred = False

        async def _reply(text: str):
            if deferred:
                await interaction.followup.send(text, ephemeral=True)
                return
            try:
                await interaction.response.send_message(text, ephemeral=True)
            except discord.errors.InteractionResponded:
                await interaction.followup.send(text, ephemeral=True)

        db = self.bot.get_cog("Database")
        jio_cog = self.bot.get_cog("Jio")
        if not db or not jio_cog:
            await _reply("系統忙碌中，請稍後再試。")
            return

        event = await db.get_event(self.event_id)
        participant = await db.get_participant(self.event_id, self.user_id)
        if not event or not participant:
            await _reply("找不到面試資料。")
            return

        if event.get("cancelled") or event.get("workflow_state") in {"CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"}:
            await _reply("活動已結束，無法再修改答案。")
            return

        interview = participant.get("interview", {}) or {}
        if interview.get("completed") or interview.get("confirmed"):
            await _reply("你已確認送出，無法再修改。")
            return

        question_ids = {str(q.get("id") or "") for q in (event.get("interview_questions", []) or [])}
        if str(self.question_id) not in question_ids:
            await _reply("找不到該題目，請重新開啟確認卡片。")
            return

        new_answer = str(self.children[1].value or "").strip()
        if not new_answer:
            await _reply("答案不可為空白。")
            return

        answers = dict(interview.get("answers", {}) or {})
        old_answer = str(answers.get(self.question_id) or "").strip()
        answers[self.question_id] = new_answer

        await db.update_participant_interview(
            self.event_id,
            self.user_id,
            answers=answers,
            current_question_id="confirm_submit",
            interview_completed=False,
            confirmed=False,
        )
        await db.update_participant_status(self.event_id, self.user_id, "INTERVIEWING")
        await db.append_history(
            self.event_id,
            self.user_id,
            "system",
            f"Participant manually edited answer for {self.question_id}: '{old_answer}' -> '{new_answer}'",
            targets=[self.user_id],
            question_id=self.question_id,
            message_type="system_event",
        )

        if self.source_message:
            await jio_cog.send_submission_review(
                self.event_id,
                self.user_id,
                message_to_edit=self.source_message,
            )

        await jio_cog.update_dashboard(self.event_id)
        await jio_cog.log_event_state(self.event_id)
        await _reply("✅ 已更新該題答案，請再次確認所有內容後按 Confirm。")


class ConfirmEditQuestionView(View):
    def __init__(self, bot, event_id, user_id, options):
        super().__init__(timeout=600)
        self.add_item(ConfirmEditQuestionSelect(bot, event_id, user_id, options))


class ConfirmSubmissionView(View):
    def __init__(self, bot, event_id, user_id, options=None):
        super().__init__(timeout=600)
        self.bot = bot
        self.event_id = event_id
        self.user_id = user_id
        if options:
            self.add_item(ConfirmEditQuestionSelect(bot, event_id, user_id, options))

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green, row=1)
    async def confirm_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的確認按鈕。", ephemeral=True)
            return

        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        deferred = False
        try:
            await interaction.response.defer(ephemeral=True)
            deferred = True
        except discord.errors.InteractionResponded:
            deferred = True
        except Exception:
            deferred = False

        self.clear_items()
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        await jio_cog.confirm_submission_from_button(
            self.event_id,
            self.user_id,
            interaction,
            deferred=deferred,
        )



class Jio(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _remaining_interview_minutes(self, event):
        deadline = (event or {}).get("interview_deadline")
        if not deadline:
            return None

        now = datetime.datetime.utcnow()
        if getattr(deadline, "tzinfo", None) is not None:
            now = now.replace(tzinfo=deadline.tzinfo)
        remaining = int(max(0, (deadline - now).total_seconds() // 60))
        return remaining

    async def _resolve_channel(self, channel_id):
        if not channel_id:
            return None

        channel = self.bot.get_channel(channel_id)
        if channel:
            return channel

        try:
            return await self.bot.fetch_channel(channel_id)
        except discord.NotFound:
            print(f"[DEBUG] Channel {channel_id} not found")
        except discord.Forbidden:
            print(f"[DEBUG] Missing permission to access channel {channel_id}")
        except Exception as e:
            print(f"[DEBUG] Failed to fetch channel {channel_id}: {e}")
        return None

    async def _get_event_by_id_str(self, event_id):
        if not event_id:
            return None

        db = self.bot.get_cog("Database")
        if not db:
            return None

        if hasattr(db, "get_event_by_id_str"):
            try:
                return await db.get_event_by_id_str(str(event_id))
            except Exception:
                return None

        try:
            return await db.get_event(ObjectId(str(event_id)))
        except Exception:
            return None

    def _get_participant_from_event(self, event, user_id):
        for participant in (event or {}).get("participants", []) or []:
            if participant.get("user_id") == user_id:
                return participant
        return None

    def _get_current_question_text(self, event, participant):
        interview = (participant or {}).get("interview", {}) or {}
        current_qid = str(interview.get("current_question_id") or "").strip()
        qmap = {str(q.get("id")): str(q.get("text") or "") for q in ((event or {}).get("interview_questions", []) or [])}

        if current_qid and current_qid in qmap:
            return qmap[current_qid]

        first_question = ((event or {}).get("interview_questions", []) or [None])[0]
        if first_question:
            return str(first_question.get("text") or "")
        return ""

    async def promote_next_queued_event_for_user(self, user_id, completed_event_id=None):
        db = self.bot.get_cog("Database")
        if not db:
            return None

        if completed_event_id is not None:
            await db.remove_participating_event(user_id, str(completed_event_id))

        focus_event_id = await db.get_focus_event(user_id)
        if focus_event_id:
            focus_event = await self._get_event_by_id_str(focus_event_id)
            focus_participant = self._get_participant_from_event(focus_event, user_id)
            focus_interview = (focus_participant or {}).get("interview", {}) or {}
            focus_status = str((focus_participant or {}).get("status") or "").strip().upper()
            if (
                focus_event
                and not focus_event.get("cancelled")
                and str(focus_event.get("workflow_state") or "").strip().upper() not in {"CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"}
                and str(focus_event.get("adjudication_status") or "").strip().upper() not in {"DECIDED", "CANCELLED"}
                and focus_status in {"INTERVIEWING", "ON_HOLD"}
                and not focus_interview.get("completed")
            ):
                return focus_event

        participating_events = await db.get_participating_events(user_id)
        if not participating_events:
            await db.clear_user_active_event(user_id)
            return None

        target_event = None
        target_entry = None
        for entry in participating_events:
            event_id = str(entry.get("event_id") or "").strip()
            if not event_id:
                continue

            event = await self._get_event_by_id_str(event_id)
            participant = self._get_participant_from_event(event, user_id)
            interview = (participant or {}).get("interview", {}) or {}
            status = str((participant or {}).get("status") or "").strip().upper()

            if (
                not event
                or event.get("cancelled")
                or str(event.get("workflow_state") or "").strip().upper() in {"CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"}
                or str(event.get("adjudication_status") or "").strip().upper() in {"DECIDED", "CANCELLED"}
                or not participant
                or interview.get("completed")
                or status in {"READY", "FINISHED", "KICKED", "DECLINED"}
            ):
                await db.remove_participating_event(user_id, event_id)
                continue

            if event.get("active", True):
                continue

            if status in {"IN_QUEUE", "INTERVIEWING", "ON_HOLD"}:
                target_event = event
                target_entry = entry
                break

        if not target_event or not target_entry:
            await db.clear_user_active_event(user_id)
            return None

        target_event_id = str(target_entry.get("event_id"))
        target_status = str((target_entry.get("status") or "").strip().upper() or "INTERVIEWING")

        if target_status == "IN_QUEUE":
            await db.update_participant_status(target_event["_id"], user_id, "INTERVIEWING")
            await db.set_participant_reply_status(target_event["_id"], user_id, "WAITING_FOR_REPLY")
            await db.set_participating_event_status(user_id, target_event_id, "INTERVIEWING")

            question_text = self._get_current_question_text(target_event, self._get_participant_from_event(target_event, user_id))
            remain = self._remaining_interview_minutes(target_event)
            remain_tip = f"\n⏳ 面試剩餘時間：約 {remain} 分鐘" if remain is not None else ""

            user = self.bot.get_user(user_id)
            if not user:
                try:
                    user = await self.bot.fetch_user(user_id)
                except Exception:
                    user = None

            if user and question_text:
                try:
                    await user.send(
                        f"⏭️ 已自動切換到下一個活動：{target_event.get('title', '未命名活動')}\n"
                        f"➡️ 請從目前題目繼續：{question_text}{remain_tip}"
                    )
                except Exception:
                    pass

        await db.set_focus_event(user_id, target_event_id, status="INTERVIEWING")
        return target_event

    async def disable_management_view(self, event_id):
        db = self.bot.get_cog("Database")
        if not db:
            return

        event = await db.get_event(event_id)
        if not event:
            return

        channel = await self._resolve_channel(event.get("channel_id"))
        message_id = event.get("message_id")
        if channel and message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(view=None)
            except Exception:
                pass

        # Disable adjudication DM buttons as well (e.g., 方案 1 / 方案 2)
        adjudication_channel_id = event.get("adjudication_view_channel_id")
        adjudication_message_id = event.get("adjudication_view_message_id")
        if adjudication_channel_id and adjudication_message_id:
            adjudication_channel = self.bot.get_channel(adjudication_channel_id)
            if not adjudication_channel:
                try:
                    adjudication_channel = await self.bot.fetch_channel(adjudication_channel_id)
                except Exception:
                    adjudication_channel = None

            if adjudication_channel:
                try:
                    adjudication_message = await adjudication_channel.fetch_message(adjudication_message_id)
                    await adjudication_message.edit(view=None)
                except Exception:
                    pass

            try:
                await db.events.update_one(
                    {"_id": event_id},
                    {
                        "$unset": {
                            "adjudication_view_channel_id": "",
                            "adjudication_view_message_id": "",
                        }
                    },
                )
            except Exception:
                pass

    def _build_submission_edit_options(self, event, participant):
        questions = (event or {}).get("interview_questions", []) or []
        answers = ((participant or {}).get("interview", {}) or {}).get("answers", {}) or {}

        options = []
        for idx, question in enumerate(questions, start=1):
            qid = str(question.get("id") or "")
            qtext = str(question.get("text") or "").strip()
            if not qid or not qtext:
                continue

            answer_preview = str(answers.get(qid) or "尚未填寫").replace("\n", " ").strip()
            options.append(
                discord.SelectOption(
                    label=f"{idx}. {qtext}"[:100],
                    value=qid,
                    description=f"目前答案：{answer_preview}"[:100],
                )
            )
        return options

    async def build_submission_review_embed(self, event_id, user_id, event=None, participant=None):
        db = self.bot.get_cog("Database")
        event = event or (await db.get_event(event_id) if db else None)
        participant = participant or (await db.get_participant(event_id, user_id) if db else None)

        embed = discord.Embed(
            title=f"🧾 最終確認 | {event.get('title', '未命名活動') if event else '活動'}",
            description="請先確認所有答案。你可以直接用下拉選單選題，打開表單手動修改；內容正確後再按 Confirm。",
            color=0x9B59B6,
        )
        if not event or not participant:
            embed.description = "找不到面試資料。"
            return embed

        questions = event.get("interview_questions", []) or []
        answers = ((participant.get("interview", {}) or {}).get("answers", {}) or {})

        if not questions:
            embed.add_field(name="題目", value="(無題目)", inline=False)
            return embed

        for idx, question in enumerate(questions, start=1):
            qid = str(question.get("id") or "")
            qtext = str(question.get("text") or "").strip() or f"題目 {idx}"
            answer = str(answers.get(qid) or "（尚未填寫）").strip() or "（尚未填寫）"
            embed.add_field(name=f"{idx}. {qtext}"[:256], value=answer[:1000], inline=False)

        remain = self._remaining_interview_minutes(event)
        if remain is not None:
            embed.set_footer(text=f"面試剩餘時間：約 {remain} 分鐘")
        return embed

    async def build_confirm_submission_view(self, event_id, user_id, event=None, participant=None):
        db = self.bot.get_cog("Database")
        event = event or (await db.get_event(event_id) if db else None)
        participant = participant or (await db.get_participant(event_id, user_id) if db else None)
        options = self._build_submission_edit_options(event, participant)
        return ConfirmSubmissionView(self.bot, event_id, user_id, options=options)

    async def send_submission_review(self, event_id, user_id, user=None, message_to_edit=None, event=None, participant=None):
        db = self.bot.get_cog("Database")
        event = event or (await db.get_event(event_id) if db else None)
        participant = participant or (await db.get_participant(event_id, user_id) if db else None)

        embed = await self.build_submission_review_embed(event_id, user_id, event=event, participant=participant)
        view = await self.build_confirm_submission_view(event_id, user_id, event=event, participant=participant)

        if message_to_edit:
            await message_to_edit.edit(content=None, embed=embed, view=view)
            return message_to_edit

        target_user = user
        if not target_user:
            target_user = self.bot.get_user(int(user_id))
            if not target_user:
                try:
                    target_user = await self.bot.fetch_user(int(user_id))
                except Exception:
                    target_user = None
        if not target_user:
            return None

        return await target_user.send(embed=embed, view=view)

    async def confirm_submission_from_button(self, event_id, user_id, interaction: discord.Interaction, deferred=False):
        async def _reply(text: str):
            if deferred:
                await interaction.followup.send(text, ephemeral=True)
                return
            try:
                await interaction.response.send_message(text, ephemeral=True)
            except discord.errors.InteractionResponded:
                await interaction.followup.send(text, ephemeral=True)

        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id) if db else None
        participant = await db.get_participant(event_id, user_id) if db else None
        if not event or not participant:
            await _reply("找不到活動，請稍後再試。")
            return

        interview = (participant.get("interview", {}) or {})
        if interview.get("completed") or participant.get("status") in {"READY", "FINISHED"}:
            await _reply("你已完成確認送出。")
            return

        await db.update_participant_interview(
            event_id,
            user_id,
            current_question_id="completed",
            interview_completed=True,
            confirmed=True,
        )
        await db.update_participant_status(event_id, user_id, "READY")
        await db.append_history(
            event_id,
            user_id,
            "system",
            "Participant confirmed final submission.",
            targets=[user_id],
            question_id="confirm_submit",
            message_type="system_event",
        )
        await db.remove_participating_event(user_id, str(event_id))
        await self.promote_next_queued_event_for_user(user_id)

        try:
            await interaction.user.send("✅ 已確認送出你的最終訪談結果。")
        except Exception:
            pass

        await self.maybe_trigger_adjudication(event_id)
        await self.update_dashboard(event_id)
        await self.log_event_state(event_id)
        await _reply("✅ 已確認送出。")

    async def open_edit_question_selector(self, event_id, user_id, interaction: discord.Interaction):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id) if db else None
        participant = await db.get_participant(event_id, user_id) if db else None
        if not event or not participant:
            await interaction.followup.send("找不到面試資料。", ephemeral=True)
            return

        interview = participant.get("interview", {}) or {}
        if interview.get("confirmed") or interview.get("completed"):
            await interaction.followup.send("你已確認送出，無法再修改。", ephemeral=True)
            return

        options = self._build_submission_edit_options(event, participant)
        if not options:
            await interaction.followup.send("目前沒有可修改的題目。", ephemeral=True)
            return

        view = ConfirmEditQuestionView(self.bot, event_id, user_id, options)
        await interaction.followup.send("請選擇要修改的題目。", view=view, ephemeral=True)

    async def open_manual_edit_modal(self, event_id, user_id, question_id, interaction: discord.Interaction, source_view=None, source_message=None):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id) if db else None
        participant = await db.get_participant(event_id, user_id) if db else None
        if not event or not participant:
            await interaction.response.send_message("找不到面試資料。", ephemeral=True)
            return

        interview = participant.get("interview", {}) or {}
        if interview.get("confirmed") or interview.get("completed"):
            await interaction.response.send_message("你已確認送出，無法再修改。", ephemeral=True)
            return

        question_text = ""
        for question in event.get("interview_questions", []) or []:
            if str(question.get("id") or "") == str(question_id):
                question_text = str(question.get("text") or "")
                break

        if not question_text:
            await interaction.response.send_message("找不到該題目，請重新選擇。", ephemeral=True)
            return

        current_answer = str((interview.get("answers", {}) or {}).get(question_id) or "")
        modal = ManualAnswerEditModal(
            self.bot,
            event_id,
            user_id,
            question_id,
            question_text,
            current_answer,
            source_view=source_view,
            source_message=source_message,
            title="手動修改答案",
        )
        await interaction.response.send_modal(modal)

    async def enter_edit_question_mode(self, event_id, user_id, question_id, interaction: discord.Interaction):
        # Keep backward compatibility for old call sites, but route to modal-based manual editing.
        await self.open_manual_edit_modal(event_id, user_id, question_id, interaction)

    async def cancel_event_with_announcement(self, event_id, cancelled_by, reason="", source="HOST"):
        db = self.bot.get_cog("Database")
        if not db:
            return False

        event = await db.cancel_event(event_id, cancelled_by=cancelled_by, reason=reason)
        if not event:
            return False

        channel = await self._resolve_channel(event.get("channel_id"))
        if channel:
            text = (
                f"🛑 活動 **{event.get('title', '未命名活動')}** 已取消。\n"
                f"原因：{event.get('cancel_reason') or '未提供'}\n"
                f"來源：{source}"
            )
            try:
                await channel.send(text)
            except Exception:
                pass

        await self.disable_management_view(event_id)

        for participant in event.get("participants", []):
            uid = participant.get("user_id")
            if uid:
                await db.remove_participating_event(uid, str(event_id))
                await self.promote_next_queued_event_for_user(uid)

        return True

    async def describe_current_interview_state(self, event, user_id):
        questions = event.get("interview_questions", []) or []
        participant = None
        for p in event.get("participants", []):
            if p.get("user_id") == user_id:
                participant = p
                break

        if not participant:
            return "目前未找到你的參與狀態。"

        interview = participant.get("interview", {}) or {}
        current_qid = interview.get("current_question_id")
        qmap = {str(q.get("id")): q for q in questions}

        if current_qid == "confirm_submit":
            return "你目前在最後確認階段。請使用下方按鈕 Confirm 或 Edit。"

        if current_qid == "completed" or interview.get("completed"):
            return "你目前已完成面試。"

        current_idx = 1
        total = max(1, len(questions))
        current_question_text = ""
        for idx, question in enumerate(questions, start=1):
            if str(question.get("id")) == str(current_qid):
                current_idx = idx
                current_question_text = str(question.get("text") or "")
                break

        if not current_question_text and questions:
            current_question_text = str(questions[0].get("text") or "")

        return f"目前進度：第 {current_idx}/{total} 題\n目前題目：{current_question_text or '尚未開始'}"

    async def build_interview_progress_embed(self, event_id):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id)
        embed = discord.Embed(
            title=f"📊 面試進度 | {event.get('title', '未命名活動') if event else '活動'}",
            color=0x3498db,
        )
        if not event:
            embed.description = "找不到活動。"
            return embed

        questions = event.get("interview_questions", []) or []
        total = max(1, len(questions))
        qids = [str(q.get("id")) for q in questions]

        lines = []
        for participant in event.get("participants", []):
            uid = participant.get("user_id")
            user = self.bot.get_user(uid)
            if not user:
                try:
                    user = await self.bot.fetch_user(uid)
                except Exception:
                    user = None
            name = user.display_name if user else f"User {uid}"
            interview = participant.get("interview", {}) or {}
            current_qid = str(interview.get("current_question_id") or "")
            completed = bool(interview.get("completed"))

            if completed or current_qid == "completed":
                ratio = f"{total}/{total}"
                status = "✅"
            elif current_qid == "confirm_submit":
                ratio = f"{total}/{total}"
                status = "📝確認中"
            else:
                idx = 1
                if current_qid in qids:
                    idx = qids.index(current_qid) + 1
                ratio = f"{idx}/{total}"
                status = participant.get("status", "UNKNOWN")

            lines.append(f"- {name}: {ratio} ({status})")

        embed.description = "\n".join(lines) if lines else "尚無參與者。"
        return embed

    async def end_interview_early(self, event_id, trigger_user=None):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id)
        if not event:
            return

        kicked_ids = await db.auto_kick_interview_overdue(event_id)
        failed_event = await db.fail_event_min_participants_by_ready(event_id)
        channel = await self._resolve_channel(event.get("channel_id"))

        if failed_event:
            if channel:
                try:
                    await channel.send(
                        f"❌ 活動 **{failed_event.get('title', '未命名活動')}** 取消：{failed_event.get('cancel_reason')}"
                    )
                except Exception:
                    pass
            await self.disable_management_view(event_id)
            await self.update_dashboard(event_id)
            return

        if channel:
            try:
                opener = f"⚡ {trigger_user.mention} 已提早結束面試。" if trigger_user else "⚡ 已提早結束面試。"
                suffix = f"\n以下成員因未完成訪談而移出：{' '.join([f'<@{uid}>' for uid in kicked_ids])}" if kicked_ids else ""
                await channel.send(opener + suffix)
            except Exception:
                pass

        for uid in kicked_ids:
            await self.promote_next_queued_event_for_user(uid, completed_event_id=event_id)

        await self.maybe_trigger_adjudication(event_id)
        await self.update_dashboard(event_id)

    async def open_hold_review_ui(self, event_id, interaction: discord.Interaction):
        db = self.bot.get_cog("Database")
        if not db:
            await interaction.response.send_message("資料庫模組不可用", ephemeral=True)
            return

        event = await db.get_event(event_id)
        if not event:
            await interaction.response.send_message("活動不存在", ephemeral=True)
            return

        hold_list = await db.get_on_hold_participants(event_id)
        if not hold_list:
            await interaction.response.send_message("目前沒有 ON_HOLD 參與者。", ephemeral=True)
            return

        candidates = []
        for p in hold_list:
            uid = p.get("user_id")
            user = self.bot.get_user(uid)
            if not user:
                try:
                    user = await self.bot.fetch_user(uid)
                except Exception:
                    user = None
            candidates.append(
                {
                    "user_id": uid,
                    "display_name": user.display_name if user else f"User {uid}",
                    "hold_context": p.get("hold_context") or {},
                }
            )

        view = HoldKickView(self.bot, event_id, candidates)
        await interaction.response.send_message(
            "請選擇要處理的 [停權狀態] 參與者，並填寫原因。",
            view=view,
            ephemeral=True,
        )

    async def _send_initial_interview_prompts(self, event, event_channel=None):
        db = self.bot.get_cog("Database")
        if not db:
            return

        event_id = event.get("_id")
        prompts = []
        targets = []

        eligible_status = {"INTERVIEWING", "PENDING", "JOINED", "IN_QUEUE"}
        interview_questions = event.get("interview_questions", []) or []
        first_question = interview_questions[0] if interview_questions else None
        initiator_id = event.get("initiator_id")
        current_event_id = str(event_id)

        host_name = "Unknown Host"
        if event_channel and hasattr(event_channel, "guild") and event_channel.guild:
            member = event_channel.guild.get_member(initiator_id)
            if member:
                host_name = member.display_name
        else:
            host_user = self.bot.get_user(initiator_id)
            if host_user:
                host_name = host_user.display_name

        question_lines = []
        for idx, question in enumerate(interview_questions, start=1):
            qtext = str(question.get("text") or "").strip()
            if not qtext:
                continue
            marker = "➡️ " if idx == 1 else ""
            question_lines.append(f"{marker}{idx}. {qtext}")

        question_overview = "\n".join(question_lines) if question_lines else "本活動無需額外提問。"
        if len(question_overview) > 1000:
            question_overview = question_overview[:980] + "\n..."

        base_embed = discord.Embed(
            title=f"👋 訪談開始 | {event.get('title', '未命名活動')} by {host_name}",
            description="先給你完整題目列表，接著我會再用另一則訊息開始逐題提問。",
            color=0x2ecc71,
        )
        base_embed.add_field(name=f"主揪 {host_name}", value=event.get("description", "（無）")[:1000], inline=False)
        base_embed.add_field(name="訪談題目總覽", value=question_overview, inline=False)

        for participant in event.get("participants", []):
            status = participant.get("status")
            if status not in eligible_status:
                continue

            uid = participant.get("user_id")
            if not uid:
                continue

            targets.append(uid)

            try:
                user = await self.bot.fetch_user(uid)
                # Ensure participant state enters interview flow even if previous update missed.
                if first_question:
                    focus_event_id = await db.get_focus_event(uid)
                    should_queue = False
                    if focus_event_id and str(focus_event_id) != current_event_id:
                        focus_event = await self._get_event_by_id_str(focus_event_id)
                        focus_participant = self._get_participant_from_event(focus_event, uid)
                        focus_interview = (focus_participant or {}).get("interview", {}) or {}
                        focus_status = str((focus_participant or {}).get("status") or "").strip().upper()
                        should_queue = (
                            bool(focus_event)
                            and not focus_event.get("cancelled")
                            and str(focus_event.get("workflow_state") or "").strip().upper() not in {"CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"}
                            and str(focus_event.get("adjudication_status") or "").strip().upper() not in {"DECIDED", "CANCELLED"}
                            and not focus_interview.get("completed")
                            and focus_status in {"INTERVIEWING", "ON_HOLD"}
                        )

                    await db.update_participant_interview(
                        event_id,
                        uid,
                        current_question_id=first_question.get("id"),
                        interview_completed=False,
                        confirmed=False,
                    )

                    if should_queue:
                        await db.update_participant_status(event_id, uid, "IN_QUEUE")
                        await db.set_participant_reply_status(event_id, uid, "NONE")
                        await db.add_participating_event(uid, current_event_id, status="IN_QUEUE", set_focus=False)
                        await user.send("🕒 你目前仍在另一個活動面試中，這個活動已加入佇列。完成目前面試後會自動接續。")
                    else:
                        await user.send(embed=base_embed)
                        await db.update_participant_status(event_id, uid, "INTERVIEWING")
                        await db.set_participant_reply_status(event_id, uid, "WAITING_FOR_REPLY")
                        await db.set_focus_event(uid, current_event_id, status="INTERVIEWING")
                        remaining = self._remaining_interview_minutes(event)
                        remain_tip = f"\n⏳ 面試剩餘時間：約 {remaining} 分鐘" if remaining is not None else ""
                        await user.send(f"➡️ 第 1 題：{first_question.get('text', '')}{remain_tip}")
                else:
                    await user.send(embed=base_embed)
                    await db.update_participant_interview(
                        event_id,
                        uid,
                        current_question_id="completed",
                        interview_completed=True,
                        confirmed=True,
                    )
                    await db.update_participant_status(event_id, uid, "READY")
                    await db.remove_participating_event(uid, current_event_id)
                prompts.append(uid)
                print(f"[DEBUG] Initial interview DM sent to {uid}")
            except discord.Forbidden:
                print(f"[WARN] Cannot DM user {uid}: user privacy settings block server DMs")
                if event_channel:
                    try:
                        await event_channel.send(
                            f"⚠️ <@{uid}> 無法收到私訊面試，請開啟伺服器私訊後再回覆我。"
                        )
                    except Exception as fallback_err:
                        print(f"[WARN] Failed DM fallback notify for {uid}: {fallback_err}")
            except Exception as e:
                print(f"[WARN] Failed to send initial interview DM to {uid}: {e}")

        if prompts:
            first_text = first_question.get("text", "無") if first_question else "無"
            await db.append_history(
                event_id,
                None,
                "model",
                f"Interview started. First question: {first_text}",
                targets=prompts,
                question_id=first_question.get("id") if first_question else None,
                message_type="system_event",
            )
        else:
            print(f"[WARN] No initial interview DM sent. Eligible targets: {targets}")
    
    async def build_context_string(self, db, event_id, exclude_user_id):
        # Same logic as above
        event = await db.get_event(event_id)
        if not event: return ""
        
        summary_lines = []
        for p in event.get("participants", []):
            if p["user_id"] == exclude_user_id: continue
            
            uid = p["user_id"]
            user = self.bot.get_user(uid)
            name = user.display_name if user else f"User{uid}"

            answers = (p.get("interview", {}) or {}).get("answers", {}) or {}
            answer_count = len([v for v in answers.values() if str(v).strip()])
            status_desc = f" (已回覆 {answer_count} 題)"
            
            summary_lines.append(f"- {name}{status_desc}")
            
        return "\n".join(summary_lines)

    async def collect_candidate_events_for_user(self, user_id):
        db = self.bot.get_cog("Database")

        # This query is designed to find events that the user is currently participating in, where the event is not cancelled, not in a final workflow state, and the user's participation status is either INTERVIEWING or ON_HOLD with an incomplete interview. 
        # The results are sorted by event ID in descending order (most recent first).
        cursor = db.events.find(
            {
                "cancelled": {"$ne": True},
                "workflow_state": {"$nin": ["CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"]},
                "adjudication_status": {"$nin": ["DECIDED", "CANCELLED"]},
                "participants.user_id": user_id,
                "participants": {
                    "$elemMatch": {
                        "user_id": user_id,
                        "status": {"$in": ["INTERVIEWING", "ON_HOLD"]},
                        "interview.completed": {"$ne": True},
                    }
                }
            }
        ).sort("_id", -1)
        return await cursor.to_list(length=20)

    async def resolve_event_for_dm(self, user_id, message_text):
        db = self.bot.get_cog("Database")
        events = await self.collect_candidate_events_for_user(user_id)
        if not events:
            return None

        focus_event_id = await db.get_focus_event(user_id)

        event_by_id = {str(event["_id"]): event for event in events}

        # First try to use the focus event if it's still valid
        if focus_event_id and focus_event_id in event_by_id:
            return event_by_id[focus_event_id]

        participating_events = await db.get_participating_events(user_id)

        # If no valid focus event, try to find the most recent participating event that is still active,
        # and has the user in INTERVIEWING or ON_HOLD status with incomplete interview.
        for entry in participating_events:
            event_id = str(entry.get("event_id") or "").strip()
            status = str(entry.get("status") or "").strip().upper()
            if event_id in event_by_id and status in {"INTERVIEWING", "ON_HOLD"}:
                await db.set_focus_event(user_id, event_id, status=status)
                return event_by_id[event_id]

        # As a fallback, just take the most recent event from the candidate list and set it as focus.
        selected_event = events[0]
        await db.set_focus_event(user_id, str(selected_event["_id"]), status="INTERVIEWING")
        return selected_event

    def _merge_answers_with_event_seeds(self, participant, event_seeds):
        answers = dict((participant.get("interview", {}) or {}).get("answers", {}) or {})
        seeds = event_seeds or {}

        for topic in CORE_TOPICS:
            answer_key = f"core_{topic}"
            if str(answers.get(answer_key) or "").strip():
                continue

            seed_value = str(seeds.get(topic) or "").strip()
            if seed_value:
                answers[answer_key] = seed_value

        return answers

    def build_nsw_candidates(self, participants, event_seeds=None):
        users = []
        what_options = set()
        where_options = set()
        when_options = set()
        how_options = set()

        for participant in participants:
            if participant.get("status") in ["KICKED", "DECLINED", "FINISHED"]:
                continue

            preferences = self._merge_answers_with_event_seeds(participant, event_seeds)
            weights = participant.get("weights", {}) or {}
            users.append(
                UserProfile(
                    user_id=participant.get("user_id"),
                    preferences=preferences,
                    dealbreakers=participant.get("dealbreakers", []) or [],
                    weights=weights,
                )
            )

            what_options.add(preferences.get("core_what") or "活動內容待定")
            where_options.add(preferences.get("core_where") or "地點待定")
            when_options.add(preferences.get("core_when") or "時間待定")
            how_options.add(preferences.get("core_how") or "流程待定")

        if not users:
            return []

        candidates = []
        for what in list(what_options)[:3]:
            for where in list(where_options)[:3]:
                for when in list(when_options)[:3]:
                    for how in list(how_options)[:3]:
                        candidates.append(CandidatePlan(what=what, where=where, when=when, budget=how))

        if not candidates:
            return []

        return rank_candidates(users, candidates, top_k=2)

    def _is_flagged_private_note(self, note: str) -> bool:
        lowered = str(note or "").lower()
        keywords = ["風險", "惡意", "注入", "違規", "on_hold", "malicious", "unsafe", "warning"]
        return any(token in lowered for token in keywords)

    async def maybe_trigger_adjudication(self, event_id):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id)
        if not event:
            return

        if event.get("adjudication_status") not in [None, "PENDING"]:
            return

        participants = [p for p in event.get("participants", []) if p.get("status") not in ["KICKED", "DECLINED", "FINISHED"]]
        if not participants:
            return

        all_completed = all((p.get("interview", {}) or {}).get("completed") for p in participants)
        deadline_reached = False
        deadline = event.get("interview_deadline")
        if deadline:
            now = datetime.datetime.utcnow()
            if deadline.tzinfo is not None:
                now = now.replace(tzinfo=deadline.tzinfo)
            deadline_reached = now >= deadline

        if not all_completed and not deadline_reached:
            return

        if all_completed and not event.get("interview_ended_early_announced"):
            await db.events.update_one(
                {"_id": event_id},
                {"$set": {"interview_ended_early_announced": True}},
            )
            channel = await self._resolve_channel(event.get("channel_id"))
            if channel:
                try:
                    await channel.send("⚡ 所有參與者都已送出訪談，已提早結束面試並進入裁決流程。")
                except Exception:
                    pass

        await self.send_adjudication_report(event_id)

    async def send_adjudication_report(self, event_id):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id)
        if not event:
            return

        event_seeds = event.get("activity_seeds", {}) or {}
        candidates = self.build_nsw_candidates(event.get("participants", []), event_seeds=event_seeds)
        if not candidates:
            return

        await db.events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "adjudication_candidates": candidates,
                    "adjudication_status": "AWAITING_HOST_CHOICE"
                }
            }
        )

        initiator_id = event.get("initiator_id")
        await db.set_focus_event(initiator_id, str(event_id), status="INTERVIEWING")

        user = self.bot.get_user(initiator_id)
        if not user:
            try:
                user = await self.bot.fetch_user(initiator_id)
            except Exception:
                user = None

        if not user:
            return

        report_lines = []

        questions = event.get("interview_questions", []) or []
        for participant in event.get("participants", []):
            if participant.get("status") in ["DECLINED", "FINISHED"]:
                continue

            uid = participant.get("user_id")
            user_obj = self.bot.get_user(uid)
            if not user_obj:
                try:
                    user_obj = await self.bot.fetch_user(uid)
                except Exception:
                    user_obj = None
            name = user_obj.display_name if user_obj else f"User {uid}"
            status = participant.get("status", "UNKNOWN")
            answers = self._merge_answers_with_event_seeds(participant, event_seeds)
            notes = participant.get("notes", {}) or {}

            report_lines.append(f"👤 {name} ({status})")
            for idx, question in enumerate(questions, start=1):
                qid = question.get("id")
                qtext = question.get("text", "")
                avalue = str(answers.get(qid) or "(未填)")
                report_lines.append(f"{idx}. {qtext}")
                report_lines.append(f"   ↳ {avalue}")

            report_lines.append("   4W（含活動已決定 seeds）:")
            report_lines.append(f"   - What: {str(answers.get('core_what') or '活動內容待定')}")
            report_lines.append(f"   - Where: {str(answers.get('core_where') or '地點待定')}")
            report_lines.append(f"   - When: {str(answers.get('core_when') or '時間待定')}")
            report_lines.append(f"   - How: {str(answers.get('core_how') or '流程待定')}")

            public_notes = notes.get("public", []) or []
            if public_notes:
                report_lines.append("   Public notes:")
                for note in public_notes[-3:]:
                    report_lines.append(f"   - {note}")

            private_notes = notes.get("private", []) or []
            flagged_private = [n for n in private_notes if self._is_flagged_private_note(n)]
            if flagged_private:
                report_lines.append("   Flagged private notes:")
                for note in flagged_private[-3:]:
                    report_lines.append(f"   - {note}")

            report_lines.append("")

        report_lines.append("🏁 NSW 候選方案")
        for index, candidate in enumerate(candidates, start=1):
            item = candidate.get("candidate", {})
            report_lines.append(
                f"方案 {index}: {item.get('what')} / {item.get('where')} / {item.get('when')} / 執行方式 {item.get('budget')}"
            )

        report_text = "\n".join(report_lines).strip()
        if len(report_text) > 3800:
            report_text = report_text[:3800] + "\n...(內容過長已截斷)"

        report_embed = discord.Embed(
            title=f"📋 幕僚報告 - {event.get('title', '未命名活動')}",
            description=report_text or "(無資料)",
            color=0x9B59B6,
        )

        choice_lines = []
        for index, candidate in enumerate(candidates, start=1):
            item = candidate.get("candidate", {})
            choice_lines.append(
                f"**方案 {index}**\n"
                f"What: {item.get('what') or '待定'}\n"
                f"Where: {item.get('where') or '待定'}\n"
                f"When: {item.get('when') or '待定'}\n"
                f"How: {item.get('how') or item.get('budget') or '待定'}"
            )

        choice_embed = discord.Embed(
            title="請選擇最終方案",
            description="\n\n".join(choice_lines)[:3800],
            color=0x9B59B6,
        )

        view = AdjudicationView(self.bot, event_id)
        await user.send(embed=report_embed)
        choice_message = await user.send(embed=choice_embed, view=view)

        await db.events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "adjudication_view_channel_id": choice_message.channel.id,
                    "adjudication_view_message_id": choice_message.id,
                }
            },
        )

    async def apply_adjudication_choice(self, event_id, user_id, choice_index, interaction=None):
        db = self.bot.get_cog("Database")

        async def _reply(text):
            if not interaction:
                return
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(text, ephemeral=True)
                else:
                    await interaction.response.send_message(text, ephemeral=True)
            except Exception:
                pass

        event = await db.get_event(event_id)
        if not event:
            await _reply("活動不存在。")
            return

        if event.get("initiator_id") != user_id:
            await _reply("只有發起人可裁決。")
            return

        current_status = event.get("adjudication_status")
        if current_status in {"DECIDED", "DECIDING", "CANCELLED"}:
            await _reply("此活動已完成裁決，請勿重複操作。")
            return
        if current_status not in {None, "PENDING", "AWAITING_HOST_CHOICE"}:
            await _reply("目前尚未進入可裁決狀態。")
            return

        candidates = event.get("adjudication_candidates", []) or []
        idx = choice_index - 1
        if idx < 0 or idx >= len(candidates):
            await _reply("方案不存在。")
            return

        selected = candidates[idx]

        # Claim adjudication once to avoid duplicate announcements on repeated clicks.
        claim_result = await db.events.update_one(
            {
                "_id": event_id,
                "adjudication_status": {"$in": [None, "PENDING", "AWAITING_HOST_CHOICE"]},
            },
            {
                "$set": {
                    "adjudication_status": "DECIDING",
                    "adjudication_by": user_id,
                    "adjudication_at": datetime.datetime.utcnow(),
                }
            },
        )
        if getattr(claim_result, "modified_count", 0) == 0:
            await _reply("此活動已完成裁決，請勿重複操作。")
            return

        scheduled_event_id = await self.create_scheduled_event_from_plan(event, selected)

        await db.events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "workflow_state": "FINISHED",
                    "active": False,
                    "adjudication_status": "DECIDED",
                    "adjudication_result": selected,
                    "adjudication_by": user_id,
                    "adjudication_at": datetime.datetime.utcnow(),
                    "scheduled_event_id": str(scheduled_event_id) if scheduled_event_id else None,
                }
            }
        )
        await db.events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "participants.$[elem].status": "FINISHED",
                }
            },
            array_filters=[{"elem.status": {"$in": ["PENDING", "INTERVIEWING", "IN_QUEUE", "READY", "ON_HOLD", "JOINED"]}}],
        )

        for participant in event.get("participants", []):
            uid = participant.get("user_id")
            if uid:
                await db.remove_participating_event(uid, str(event_id))
                await self.promote_next_queued_event_for_user(uid)

        await self.disable_management_view(event_id)

        channel = await self._resolve_channel(event.get("channel_id"))
        if channel:
            candidate = selected.get("candidate", {})
            ready_mentions = []
            for p in event.get("participants", []):
                if p.get("status") == "READY":
                    ready_mentions.append(f"<@{p.get('user_id')}>")

            host_name = "Unknown"
            host_id = event.get("initiator_id")
            host_user = self.bot.get_user(host_id)
            if not host_user:
                try:
                    host_user = await self.bot.fetch_user(host_id)
                except Exception:
                    host_user = None
            if host_user:
                host_name = getattr(host_user, "display_name", None) or getattr(host_user, "name", "Unknown")

            announce_embed = discord.Embed(
                title=f"📢 {event.get('title', '未命名活動')} 最終方案出爐!",
                color=0x2ECC71,
            )
            announce_embed.add_field(name="What", value=str(candidate.get("what") or "待定"), inline=False)
            announce_embed.add_field(name="Where", value=str(candidate.get("where") or "待定"), inline=False)
            announce_embed.add_field(name="When", value=str(candidate.get("when") or "待定"), inline=False)
            announce_embed.add_field(name="How", value=str(candidate.get("how") or candidate.get("budget") or "待定"), inline=False)
            announce_embed.add_field(name="主揪", value=str(host_name), inline=False)

            announce_text = None
            if scheduled_event_id:
                announce_embed.add_field(name="Discord Event", value=f"ID: {scheduled_event_id}", inline=False)
                if ready_mentions:
                    announce_text = "請以下通過面試的成員前往活動事件按 Interested：\n" + " ".join(ready_mentions)
            await channel.send(content=announce_text, embed=announce_embed)

        await _reply("✅ 已完成裁決並公告。")

    async def create_scheduled_event_from_plan(self, event, selected):
        guild = None
        channel = await self._resolve_channel(event.get("channel_id"))
        if channel and getattr(channel, "guild", None):
            guild = channel.guild

        if not guild or not channel:
            return None

        candidate = selected.get("candidate", {})
        title = f"Jio Ba｜{event.get('title', '活動')}"
        description = f"{candidate.get('what')} / {candidate.get('where')} / 方式 {candidate.get('budget')}"

        start_time = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        end_time = start_time + datetime.timedelta(hours=2)

        try:
            # py-cord 2.7 uses `location` and `ScheduledEventPrivacyLevel`.
            # If the command was run in a text channel, create an external event.
            if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                location = channel
            else:
                location = candidate.get("where") or "待定地點"

            scheduled = await guild.create_scheduled_event(
                name=title,
                description=description,
                start_time=start_time,
                end_time=end_time,
                location=location,
                privacy_level=discord.ScheduledEventPrivacyLevel.guild_only,
            )
            return scheduled.id
        except Exception as e:
            print(f"[WARN] Failed to create scheduled event: {e}")
            return None

    async def start_interview_phase(self, event_id, manual_trigger_user=None):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id)
        if not event:
            return False

        workflow_state = str(event.get("workflow_state") or "").strip().upper()
        adjudication_status = str(event.get("adjudication_status") or "").strip().upper()
        if (
            event.get("cancelled")
            or workflow_state in {"CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"}
            or adjudication_status in {"DECIDED", "CANCELLED"}
        ):
            return False

        if event.get("active") is False:
            if manual_trigger_user:
                print(f"[DEBUG] Manual start_interview_phase ignored: event {event_id} already in interview phase")
            return False

        failed_event = await db.fail_event_min_participants(event_id)
        if failed_event:
            channel = await self._resolve_channel(failed_event.get("channel_id"))
            if channel:
                try:
                    await channel.send(
                        f"❌ 活動 **{failed_event.get('title', '未命名活動')}** 取消：{failed_event.get('cancel_reason')}"
                    )
                except Exception:
                    pass
            await self.disable_management_view(event_id)
            await self.log_event_state(event_id)
            return False
            
        # 1. Mark Inactive (No more joining)
        await db.events.update_one({"_id": event_id}, {"$set": {"active": False}})
        
        # 2. Notify Channel
        event_channel = await self._resolve_channel(event.get("channel_id"))
        if event_channel and (event.get("interview_questions") or []):
            msg = f"⏰ 報名截止！開始面試..."
            if manual_trigger_user:
                msg = f"⚡ {manual_trigger_user.mention} 提前截止了報名！開始面試..."
            try:
                await event_channel.send(msg)
            except:
                pass

        # 2.5 Keep participant statuses unchanged here.
        # `_send_initial_interview_prompts` will assign INTERVIEWING or IN_QUEUE per user.
        
        # [NEW] Remove Join Button from Creation Message
        try:
            if event_channel:
                msg = await event_channel.fetch_message(event["message_id"])
                if msg:
                    # Keep host management menu visible after interview starts.
                    await msg.edit(view=ManageOnlyView(self.bot, event_id))
        except Exception as e:
            print(f"[WARN] Failed to remove join button: {e}")
        
        # Refresh event from DB to get updated statuses
        event = await db.get_event(event_id)

        status_summary = {}
        for p in event.get("participants", []):
            st = p.get("status", "<none>")
            status_summary[st] = status_summary.get(st, 0) + 1
        print(f"[DEBUG] Interview phase participant status summary: {status_summary}")

        # If there are no interview questions, finish directly and announce known info.
        interview_questions = event.get("interview_questions", []) or []
        if not interview_questions:
            seeds = event.get("activity_seeds", {}) or {}
            known_lines = []
            if str(seeds.get("what") or "").strip():
                known_lines.append(f"- What: {str(seeds.get('what')).strip()}")
            if str(seeds.get("where") or "").strip():
                known_lines.append(f"- Where: {str(seeds.get('where')).strip()}")
            if str(seeds.get("when") or "").strip():
                known_lines.append(f"- When: {str(seeds.get('when')).strip()}")
            if str(seeds.get("how") or "").strip():
                known_lines.append(f"- How: {str(seeds.get('how')).strip()}")
            known_block = "\n".join(known_lines) if known_lines else f"- 描述: {event.get('description', '（無）')}"

            await db.events.update_one(
                {"_id": event_id},
                {
                    "$set": {
                        "active": False,
                        "workflow_state": "FINISHED",
                        "adjudication_status": "DECIDED",
                        "adjudication_result": {
                            "candidate": {
                                "what": str(seeds.get("what") or "活動內容待定"),
                                "where": str(seeds.get("where") or "地點待定"),
                                "when": str(seeds.get("when") or "時間待定"),
                                "budget": str(seeds.get("how") or "流程待定"),
                            }
                        },
                    }
                },
            )
            await db.events.update_one(
                {"_id": event_id},
                {
                    "$set": {
                        "participants.$[elem].status": "FINISHED",
                        "participants.$[elem].interview.current_question_id": "completed",
                        "participants.$[elem].interview.completed": True,
                        "participants.$[elem].interview.confirmed": True,
                    }
                },
                array_filters=[{"elem.status": {"$nin": ["KICKED", "DECLINED", "FINISHED"]}}],
            )

            for participant in event.get("participants", []):
                uid = participant.get("user_id")
                if uid:
                    await db.remove_participating_event(uid, str(event_id))
                    await self.promote_next_queued_event_for_user(uid)

            if event_channel:
                try:
                    await event_channel.send(
                        "✅ 本活動無額外題目需訪談，已直接完成。\n"
                        "以下為目前已知資訊：\n"
                        f"{known_block}"
                    )
                except Exception:
                    pass

            await self.disable_management_view(event_id)
            await self.update_dashboard(event_id)
            await self.log_event_state(event_id)
            return True

        # 3. Send first interview question to all interviewing participants.
        await self._send_initial_interview_prompts(event, event_channel=event_channel)

        interview_deadline = event.get("interview_deadline")
        if interview_deadline:
            self.bot.loop.create_task(self.enforce_interview_deadline(event_id, interview_deadline))

        # 4. Formulate State for Agent
        participants_dict = {}
        for p in event.get("participants", []):
            uid = p["user_id"]
            # Try to get server nickname
            member = None
            if event_channel and hasattr(event_channel, 'guild') and event_channel.guild:
                member = event_channel.guild.get_member(uid)
            
            user = member or self.bot.get_user(uid)
            name = user.display_name if user else f"User{uid}"
            participants_dict[uid] = {
                "id": uid,
                "name": name,
                "answers": (p.get("interview", {}) or {}).get("answers", {}),
                "status": p.get("status"),
                "warning_count": p.get("warning_count", 0),
                "history": p.get("conversation_history", []),
            }

        # Do NOT invoke the graph with empty user_inputs.
        # AI processing starts when participants reply in DM (handled in on_message).

        # Update dashboard
        await self.update_dashboard(event_id)
        await self.log_event_state(event_id)
        return True


    async def update_dashboard(self, event_id):
        print(f"[DEBUG] update_dashboard called for {event_id}")
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id)
        if not event:
            print("[DEBUG] Event not found in DB")
            return

        channel = await self._resolve_channel(event.get("channel_id"))
        if not channel:
            print(f"[DEBUG] Channel {event['channel_id']} not found")
            return
        
        try:
            msg = await channel.fetch_message(event["message_id"])
            print(f"[DEBUG] Found message {msg.id}")
        except discord.NotFound:
            print("[DEBUG] Message not found (deleted?)")
            return
        except Exception as e:
            print(f"[DEBUG] Failed to fetch message: {e}")
            return

        title_text = event.get("title", "未命名活動")
        desc_text = event.get("description", "（由參與者訪談共同完善）")
        desc_text += "\n\n點擊下方按鈕加入！"

        if event.get("cancelled"):
            desc_text += f"\n\n🛑 **活動已取消**: {event.get('cancel_reason') or '未提供原因'}"

        signup_deadline = event.get("signup_deadline")
        interview_deadline = event.get("interview_deadline")
        if signup_deadline:
            ts = int(signup_deadline.replace(tzinfo=datetime.timezone.utc).timestamp())
            desc_text += f"\n\n⏳ **報名截止**: <t:{ts}:R>"
        if interview_deadline:
            ts2 = int(interview_deadline.replace(tzinfo=datetime.timezone.utc).timestamp())
            desc_text += f"\n\n🕒 **面試截止**: <t:{ts2}:R>"

        seed_text = format_activity_seeds(event.get("activity_seeds", {}))
        if seed_text:
            desc_text += f"\n\n**4W Seeds**\n{seed_text}"

        if event.get("min_participants"):
            desc_text += f"\n\n👥 **最低成團人數**: {int(event.get('min_participants'))}"

        # Find initiator for footer
        initiator_id = event.get("initiator_id")
        initiator_name = "Unknown"
        guild = channel.guild if hasattr(channel, "guild") else None
        if guild:
             mem = guild.get_member(initiator_id)
             if mem: initiator_name = mem.display_name

        embed = discord.Embed(title=f"🎯 活動: {title_text}", description=desc_text, color=0xffa500)
        embed.set_footer(text=f"發起人: {initiator_name}")
        
        # Build participants list
        participants = event.get("participants", [])
        
        text_joined = []
        text_interviewing = []
        text_in_queue = []
        text_ready = []
        text_kicked = []
        text_on_hold = []
        
        for p in participants:
            user = self.bot.get_user(p["user_id"])
            if not user and guild:
                user = guild.get_member(p["user_id"])
                
            name = user.display_name if user else f"User {p['user_id']}"
            status = p["status"]
            
            line = f"{name}"
            
            if status == "PENDING":
                text_joined.append(line)
            elif status == "INTERVIEWING":
                text_interviewing.append(line)
            elif status == "IN_QUEUE":
                text_in_queue.append(line)
            elif status == "READY":
                text_ready.append(line)
            elif status == "KICKED":
                text_kicked.append(line)
            elif status == "ON_HOLD":
                text_on_hold.append(line)
        
        if text_joined: embed.add_field(name="剛加入 (Joined)", value="\n".join(text_joined), inline=False)
        if text_interviewing: embed.add_field(name="面試中 (Interviewing)", value="\n".join(text_interviewing), inline=False)
        if text_in_queue: embed.add_field(name="排隊中 (In Queue)", value="\n".join(text_in_queue), inline=False)
        if text_on_hold: embed.add_field(name="待主揪裁決 (ON_HOLD)", value="\n".join(text_on_hold), inline=False)
        if text_ready: embed.add_field(name="準備好了 (Ready)", value="\n".join(text_ready), inline=False)
        if text_kicked: embed.add_field(name="已踢出 (Kicked)", value="\n".join(text_kicked), inline=False)
        
        if not participants:
             embed.add_field(name="參加者", value="(尚無人參加)", inline=False)
        
        try:
            await msg.edit(embed=embed)
            print("[DEBUG] Message edited successfully")
        except Exception as e:
            print(f"[DEBUG] Failed to edit message: {e}")

    @discord.slash_command(description="發起一個活動")
    async def jio(
        self, 
        ctx, 
        title: str = discord.Option(str, "活動標題 (選填，將預填入表單)", default=None, required=False),
        signup_limit: int = discord.Option(int, "報名截止分鐘 (選填)", default=None, required=False),
        interview_limit: int = discord.Option(int, "面試截止分鐘 (選填)", default=None, required=False),
        min_participants: int = discord.Option(int, "最低成團人數 (選填)", default=None, required=False),
    ):
        if ctx.guild is None:
            await ctx.respond("請在伺服器中使用 /jio。", ephemeral=True)
            return

        modal = JioCreationModal(
            self.bot, 
            ctx.channel.id, 
            title="發起活動",
            default_title=title, 
            default_signup_time=signup_limit,
            default_interview_time=interview_limit,
            default_min_participants=min_participants,
        )
        await ctx.send_modal(modal)


    async def close_event_after(self, event_id, seconds):
        await asyncio.sleep(seconds)

        db = self.bot.get_cog("Database")
        if not db:
            return

        event = await db.get_event(event_id)
        if not event:
            return

        workflow_state = str(event.get("workflow_state") or "").strip().upper()
        adjudication_status = str(event.get("adjudication_status") or "").strip().upper()
        if (
            event.get("cancelled")
            or workflow_state in {"CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"}
            or adjudication_status in {"DECIDED", "CANCELLED"}
        ):
            return

        # Auto-start interview
        jio_cog = self.bot.get_cog("Jio") 
        if jio_cog:
           await jio_cog.start_interview_phase(event_id)

    async def enforce_interview_deadline(self, event_id, deadline):
        if not deadline:
            return

        now = datetime.datetime.utcnow()
        if deadline.tzinfo is not None:
            now = now.replace(tzinfo=deadline.tzinfo)
        seconds = (deadline - now).total_seconds()

        # 睡完後執行檢查，如果已經有人觸發面試結束了，就不重複執行。
        if seconds > 0:
            await asyncio.sleep(seconds)

        db = self.bot.get_cog("Database")
        if not db:
            return

        kicked_ids = await db.auto_kick_interview_overdue(event_id)
        event = await db.get_event(event_id)
        if not event:
            return

        workflow_state = str(event.get("workflow_state") or "").strip().upper()
        adjudication_status = str(event.get("adjudication_status") or "").strip().upper()
        if (
            event.get("cancelled")
            or workflow_state in {"CANCELLED", "FAILED_MIN_PARTICIPANTS", "FINISHED"}
            or adjudication_status in {"DECIDED", "CANCELLED"}
        ):
            return

        failed_event = await db.fail_event_min_participants_by_ready(event_id)
        if failed_event:
            channel = await self._resolve_channel(failed_event.get("channel_id"))
            if channel:
                try:
                    await channel.send(
                        f"❌ 活動 **{failed_event.get('title', '未命名活動')}** 取消：{failed_event.get('cancel_reason')}"
                    )
                except Exception:
                    pass
            await self.disable_management_view(event_id)
            await self.update_dashboard(event_id)
            await self.log_event_state(event_id)
            return

        initiator_id = event.get("initiator_id")
        host = self.bot.get_user(initiator_id)
        if not host:
            try:
                host = await self.bot.fetch_user(initiator_id)
            except Exception:
                host = None

        if host and kicked_ids:
            mentions = " ".join([f"<@{uid}>" for uid in kicked_ids])
            await host.send(f"⏱️ 面試截止已到，以下成員因未完成訪談而被移出：{mentions}")

        for uid in kicked_ids:
            await self.promote_next_queued_event_for_user(uid, completed_event_id=event_id)

        await self.maybe_trigger_adjudication(event_id)
        await self.update_dashboard(event_id)
        await self.log_event_state(event_id)

    @discord.slash_command(description="開啟 ON_HOLD 裁決 UI")
    async def verdict(self, ctx):
        db = self.bot.get_cog("Database")
        if not db:
            await ctx.respond("資料庫模組不可用", ephemeral=True)
            return

        query = {
            "initiator_id": ctx.author.id,
            "participants.status": "ON_HOLD",
            "cancelled": {"$ne": True},
        }
        if ctx.channel:
            query["channel_id"] = ctx.channel.id

        event = await db.events.find_one(query, sort=[("_id", -1)])
        if not event:
            await ctx.respond("目前找不到可裁決的 ON_HOLD 名單。", ephemeral=True)
            return

        hold_list = await db.get_on_hold_participants(event["_id"])
        if not hold_list:
            await ctx.respond("目前沒有 ON_HOLD 參與者。", ephemeral=True)
            return

        candidates = []
        for p in hold_list:
            uid = p.get("user_id")
            user = self.bot.get_user(uid)
            if not user:
                try:
                    user = await self.bot.fetch_user(uid)
                except Exception:
                    user = None
            candidates.append({"user_id": uid, "display_name": user.display_name if user else f"User {uid}"})

        view = HoldKickView(self.bot, event["_id"], candidates)
        await ctx.respond(
            f"🧑‍⚖️ 活動 **{event.get('title', '未命名活動')}** 的 ON_HOLD 裁決介面（只需選人 + 填理由）",
            view=view,
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        # Identify if DM
        if message.guild is None and not message.author.bot:
            print(f"[DEBUG LOG] on_message triggered from {message.author.name}: {message.content}")
            # It's a DM
            db = self.bot.get_cog("Database")
            brain = self.bot.get_cog("AIBrain")

            resolved = await self.resolve_event_for_dm(message.author.id, message.content)
            if resolved is None:
                participating_events = await db.get_participating_events(message.author.id)
                queued_ids = [
                    str(item.get("event_id") or "").strip()
                    for item in participating_events
                    if str(item.get("status") or "").strip().upper() == "IN_QUEUE"
                ]
                if queued_ids:
                    queued_titles = []
                    for event_id in queued_ids[:3]:
                        queued_event = await self._get_event_by_id_str(event_id)
                        if queued_event:
                            queued_titles.append(queued_event.get("title", "未命名活動"))

                    suffix = f"（{', '.join(queued_titles)}）" if queued_titles else ""
                    await message.author.send(f"🕒 你目前沒有可回覆的進行中題目，仍在排隊中 {suffix}。完成目前焦點活動後會自動接續。")
                return

            event = resolved
            event_id = event["_id"]
            print(f"[DEBUG LOG] Locked onto Event ID: {event_id} for user {message.author.name}")

            if event.get("cancelled") or event.get("workflow_state") in {"CANCELLED", "FAILED_MIN_PARTICIPANTS"}:
                await message.author.send("此活動已取消，訪談已停止。")
                return

            participant = await db.get_participant(event_id, message.author.id)
            if participant and participant.get("status") == "ON_HOLD":
                await message.author.send("目前你已被暫時停權，請等待主揪裁決後再繼續。")
                return

            if participant and participant.get("status") == "IN_QUEUE":
                await message.author.send("⏳ 你在這個活動目前是排隊狀態，完成當前焦點活動後會自動接續。")
                return

            interview = (participant or {}).get("interview", {}) or {}
            if participant and (interview.get("completed") or participant.get("status") in {"READY", "FINISHED"}):
                detail = await self.describe_current_interview_state(event, message.author.id)
                await message.author.send(f"✅ 你在此活動的面試已完成。\n{detail}")
                return

            if participant and str(interview.get("current_question_id") or "") == "confirm_submit":
                await message.author.send("🧾 你目前在最終確認階段，請使用上一則確認卡片的下拉選單修改答案，完成後按 Confirm。")
                return
            
            # Log User Msg
            await db.append_history(
                event_id,
                message.author.id,
                "user",
                message.content,
                author_name=message.author.display_name,
                targets=[message.author.id],
                question_id=interview.get("current_question_id"),
                message_type="user_response",
            )
            await db.set_participant_reply_status(event_id, message.author.id, "REPLIED")

            # Invoke Agent (Via Queue)
            if brain:
                print(f"[DEBUG LOG] Queueing message for AIBrain... (Event {event_id})")
                try:
                    await brain.queue_message(str(event_id), message.author.id, message.content, message.author.display_name)
                except Exception as e:
                    print(f"[DEBUG LOG] AIBrain queue FAILED: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Update Dashboard & Log
            await self.update_dashboard(event_id)
            await self.log_event_state(event_id)


    async def log_event_state(self, event_id):
        try:
            db = self.bot.get_cog("Database")
            event = await db.get_event(event_id)
            if not event: return

            def json_serial(obj):
                if isinstance(obj, ObjectId): return str(obj)
                if isinstance(obj, set): return list(obj) # Handle set
                import datetime
                if isinstance(obj, (datetime.date, datetime.datetime)): return obj.isoformat()
                raise TypeError ("Type %s not serializable" % type(obj))

            export_data = {
                "event_id": str(event["_id"]),
                "description": event.get("description"),
                "participants": []
            }

            for p in event.get("participants", []):
                user = self.bot.get_user(p["user_id"])
                p_data = {
                    "user_id": p["user_id"],
                    "username": user.name if user else "Unknown",
                    "status": p["status"],
                    "answers": (p.get("interview", {}) or {}).get("answers", {}),
                    "warning_count": p.get("warning_count", 0),
                    "conversation_history_count": len(p.get("conversation_history", []) or []),
                }
                export_data["participants"].append(p_data)
            
            # Global history now stores system/broadcast messages only.
            export_data["global_history_count"] = len(event.get("conversation_history", []))
            export_data["workflow_state"] = event.get("workflow_state", "RECRUITING")

            # Ensure directory exists
            os.makedirs("data/events", exist_ok=True)
            
            filename = f"data/events/{str(event['_id'])}.json"

            
            # Use SYNC open to avoid aiofiles dependency error
            with open(filename, "w", encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, default=json_serial, ensure_ascii=False)
                
        except Exception as e:
            print(f"Failed to log event state: {e}")

def setup(bot):
    bot.add_cog(Jio(bot))
    
