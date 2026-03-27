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

from bson import ObjectId
from cogs.matching.nsw_calculator import CandidatePlan, UserProfile, rank_candidates


def parse_activity_brief_and_seeds(text: str):
    raw = str(text or "").strip()
    if not raw:
        return "", {}

    alias = {
        "what": "what",
        "where": "where",
        "when": "when",
        "why": "why",
        "how": "how",
        "做什麼": "what",
        "地點": "where",
        "時間": "when",
        "原因": "why",
        "方式": "how",
    }
    brief_keys = {"brief", "description", "desc", "info", "資訊", "說明"}

    seeds = {}
    brief_parts = []

    normalized = raw.replace("\n", ";")
    for chunk in normalized.split(";"):
        piece = chunk.strip()
        if not piece:
            continue

        sep = "=" if "=" in piece else ("：" if "：" in piece else None)
        if not sep:
            brief_parts.append(piece)
            continue

        key, value = piece.split(sep, 1)
        cleaned_key = key.strip().lower()
        cleaned_value = value.strip()
        if not cleaned_value:
            continue

        mapped = alias.get(cleaned_key)
        if mapped:
            seeds[mapped] = cleaned_value
        elif cleaned_key in brief_keys:
            brief_parts.append(cleaned_value)
        else:
            brief_parts.append(piece)

    return " ".join(brief_parts).strip(), seeds


def _safe_json_parse(raw_text: str):
    text = str(raw_text or "").strip()
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


async def parse_activity_brief_and_seeds_with_llm(text: str):
    raw = str(text or "").strip()
    fallback_brief, fallback_seeds = parse_activity_brief_and_seeds(raw)
    if not raw:
        return "", {}

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        merged_brief = fallback_brief or raw
        return merged_brief, fallback_seeds

    try:
        llm = ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash"),
            google_api_key=api_key,
            temperature=0,
        )

        prompt = f"""
你是活動資訊抽取器。請從使用者自由輸入內容中，抽取 4W1H 與活動簡述。
輸入可能是自然語句，不一定使用 what=... 這種格式。

使用者輸入：{raw}

只輸出 JSON：
{{
  "brief": "",
  "what": "",
  "where": "",
  "when": "",
  "why": "",
  "how": ""
}}

規則：
1) 若某欄無法判斷，留空字串。
2) brief 應是 1~2 句精簡摘要。
3) 不要輸出多餘文字。
"""

        resp = await llm.ainvoke(prompt)
        parsed = _safe_json_parse(getattr(resp, "content", ""))

        llm_seeds = {}
        for key in ["what", "where", "when", "why", "how"]:
            value = str(parsed.get(key) or "").strip()
            if value:
                llm_seeds[key] = value

        merged_seeds = {**fallback_seeds, **llm_seeds}
        brief = str(parsed.get("brief") or "").strip() or fallback_brief or raw
        return brief, merged_seeds
    except Exception:
        merged_brief = fallback_brief or raw
        return merged_brief, fallback_seeds


def format_activity_seeds(seeds: dict) -> str:
    items = []
    labels = {
        "what": "What",
        "where": "Where",
        "when": "When",
        "why": "Why",
        "how": "How",
    }
    for key in ["what", "where", "when", "why", "how"]:
        value = str((seeds or {}).get(key) or "").strip()
        if value:
            items.append(f"{labels[key]}: {value}")
    return "\n".join(items)

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
        
        self.add_item(discord.ui.InputText(
            label="活動資訊 / 4W1H seeds",
            placeholder="請自由描述活動，建議可提到 what/where/when/why/how 關鍵資訊",
            style=discord.InputTextStyle.long,
            required=False,
            min_length=0,
        ))
        
        self.add_item(discord.ui.InputText(
            label="報名截止時間（分鐘，選填）",
            placeholder="例如：30",
            style=discord.InputTextStyle.short,
            required=False,
            min_length=0,
            value=str(default_signup_time) if default_signup_time else None,
        ))

        self.add_item(discord.ui.InputText(
            label="面試截止時間（分鐘，選填）",
            placeholder="例如：90",
            style=discord.InputTextStyle.short,
            required=False,
            min_length=0,
            value=str(default_interview_time) if default_interview_time else None,
        ))

        self.add_item(discord.ui.InputText(
            label="最低成團人數（選填）",
            placeholder="例如：4",
            style=discord.InputTextStyle.short,
            required=False,
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

        parsed_brief, activity_seeds = await parse_activity_brief_and_seeds_with_llm(description)

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
                title=title,
                signup_deadline=signup_deadline,
                interview_deadline=interview_deadline,
                min_participants=min_participants if min_participants is not None else self.default_min_participants,
                activity_seeds=activity_seeds,
                custom_questions=custom_questions,
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
            embed_desc += f"\n\n**4W1H Seeds**\n{seed_text}"

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
        
        self.add_item(discord.ui.InputText(
            label="活動說明",
            value=current_desc,
            style=discord.InputTextStyle.long,
            required=False
        ))
        
        self.add_item(discord.ui.InputText(
            label="報名截止時間(分鐘，選填)",
            value=str(current_time) if current_time else "",
            placeholder="留空則不變更",
            style=discord.InputTextStyle.short,
            required=False,
            min_length=0,
        ))

        self.add_item(discord.ui.InputText(
            label="面試截止時間(分鐘，選填)",
            value="",
            placeholder="留空則不變更",
            style=discord.InputTextStyle.short,
            required=False,
            min_length=0,
        ))

        self.add_item(discord.ui.InputText(
            label="自訂問題（選填，最多1題）",
            value=str(current_custom or ""),
            placeholder="留空代表移除自訂問題",
            style=discord.InputTextStyle.long,
            required=False,
            min_length=0,
        ))
        
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        new_title = self.children[0].value
        new_desc = self.children[1].value
        signup_input = self.children[2].value
        interview_input = self.children[3].value
        custom_input = self.children[4].value
        
        db = self.bot.get_cog("Database")
        event = await db.get_event(self.event_id)
            
        update_data = {
            "title": new_title,
            "description": new_desc if new_desc else "（由大家討論決定）",
        }
        
        if str(signup_input or "").strip():
            try:
                minutes = int(str(signup_input).strip())
                new_deadline = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
                update_data["signup_deadline"] = new_deadline
            except ValueError:
                await interaction.followup.send("❌ 報名截止時間格式錯誤，僅已更新其他設定。", ephemeral=True)

        if str(interview_input or "").strip():
            try:
                minutes = int(str(interview_input).strip())
                update_data["interview_deadline"] = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
            except ValueError:
                await interaction.followup.send("❌ 面試截止時間格式錯誤，僅已更新其他設定。", ephemeral=True)

        custom_question = str(custom_input or "").strip()
        update_data["host_custom_questions"] = [custom_question] if custom_question else []

        if event and event.get("active", True):
            seeds = event.get("activity_seeds", {}) or {}
            rebuilt_questions = await db._build_interview_questions(
                title=new_title,
                description=update_data["description"],
                seeds=seeds,
                custom_questions=update_data["host_custom_questions"],
            )
            update_data["interview_questions"] = rebuilt_questions
        
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
            
        await interaction.followup.send("✅ 設定已更新！", ephemeral=True)


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

            view = EndEarlyChoiceView(self.bot, self.event_id)
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

        conflict = await db.find_conflicting_interview_event(interaction.user.id, exclude_event_id=self.event_id)
        if conflict:
            await interaction.response.send_message(
                f"⛔ 你目前仍在其他活動面試中：{conflict.get('title', '未命名活動')}\n請先完成該活動後再報名新的活動。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        db = self.bot.get_cog("Database")

        await db.add_participant(self.event_id, interaction.user.id)

        await db.append_history(
            self.event_id,
            interaction.user.id,
            "system",
            "Participant joined the activity.",
            author_name=interaction.user.display_name,
        )
        await db.update_participant_status(self.event_id, interaction.user.id, "PENDING")
        
        jio_cog = self.bot.get_cog("Jio")
        if jio_cog:
            await jio_cog.update_dashboard(self.event_id)
            await jio_cog.log_event_state(self.event_id)
            
        await interaction.followup.send("✅ 已加入活動！\n⏳ 報名截止後，Bot 會私訊你開始訪談。", ephemeral=True)


class AdjudicationView(View):
    def __init__(self, bot, event_id):
        super().__init__(timeout=86400)
        self.bot = bot
        self.event_id = event_id

    @discord.ui.button(label="採用方案 1", style=discord.ButtonStyle.green)
    async def choose_plan_1(self, button: discord.ui.Button, interaction: discord.Interaction):
        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        await jio_cog.apply_adjudication_choice(self.event_id, interaction.user.id, 1, interaction)

    @discord.ui.button(label="採用方案 2", style=discord.ButtonStyle.blurple)
    async def choose_plan_2(self, button: discord.ui.Button, interaction: discord.Interaction):
        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        await jio_cog.apply_adjudication_choice(self.event_id, interaction.user.id, 2, interaction)


class CancelEventModal(discord.ui.Modal):
    def __init__(self, bot, event_id, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.event_id = event_id

        self.add_item(discord.ui.InputText(
            label="取消原因（選填）",
            placeholder="例如：人數不足、時程變更",
            style=discord.InputTextStyle.long,
            required=False,
        ))

    async def callback(self, interaction: discord.Interaction):
        reason = self.children[0].value
        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
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
    def __init__(self, bot, event_id, target_user_id, verdict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.event_id = event_id
        self.target_user_id = target_user_id
        self.verdict = verdict

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

        await interaction.response.defer(ephemeral=True)
        reason = self.children[0].value
        ok = await db.apply_host_verdict(self.event_id, self.target_user_id, self.verdict, reason=reason)
        if not ok:
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
                    await target.send(f"主揪已裁決你離開活動。理由：{reason}")
                else:
                    event = await db.get_event(self.event_id)
                    participant = await db.get_participant(self.event_id, self.target_user_id)
                    await db.set_user_active_event(self.target_user_id, str(self.event_id))

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
                        f"主揪已裁決你可繼續訪談。理由：{reason}\n"
                        f"請繼續上一題：{question_text}{remain_tip}"
                    )
            except Exception:
                pass

        jio_cog = self.bot.get_cog("Jio")
        if jio_cog:
            await jio_cog.update_dashboard(self.event_id)

        if self.verdict == "KICK":
            await interaction.followup.send("✅ 已移出該參與者。", ephemeral=True)
        else:
            await interaction.followup.send("✅ 已裁決為可繼續訪談。", ephemeral=True)


class HoldVerdictActionView(View):
    def __init__(self, bot, event_id, target_user_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.event_id = event_id
        self.target_user_id = target_user_id

    @discord.ui.button(label="裁決繼續 (CONTINUE)", style=discord.ButtonStyle.green)
    async def continue_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = HoldVerdictReasonModal(
            self.bot,
            self.event_id,
            self.target_user_id,
            "CONTINUE",
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
            placeholder="選擇要 KICK 的 ON_HOLD 成員",
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

        view = HoldVerdictActionView(self.bot, self.event_id, uid)
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

        await db.set_user_active_event(self.user_id, str(target_event.get("_id")))
        jio_cog = self.bot.get_cog("Jio")
        detail = await jio_cog.describe_current_interview_state(target_event, self.user_id) if jio_cog else ""
        await interaction.response.send_message(f"✅ 已切換到活動：{target_event.get('title', '未命名活動')}\n\n{detail}")


class DMEventSwitchView(View):
    def __init__(self, bot, user_id, events):
        super().__init__(timeout=600)
        self.add_item(DMEventSwitchSelect(bot, user_id, events))


class EndEarlyChoiceView(View):
    def __init__(self, bot, event_id):
        super().__init__(timeout=180)
        self.bot = bot
        self.event_id = event_id

    @discord.ui.button(label="提早結束報名", style=discord.ButtonStyle.blurple)
    async def end_signup(self, button: discord.ui.Button, interaction: discord.Interaction):
        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await jio_cog.start_interview_phase(self.event_id, manual_trigger_user=interaction.user)
        await interaction.followup.send("✅ 已提前截止報名並開始面試。", ephemeral=True)

    @discord.ui.button(label="提早結束面試", style=discord.ButtonStyle.red)
    async def end_interview(self, button: discord.ui.Button, interaction: discord.Interaction):
        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await jio_cog.end_interview_early(self.event_id, trigger_user=interaction.user)
        await interaction.followup.send("✅ 已提前結束面試流程。", ephemeral=True)


class ConfirmEditQuestionSelect(discord.ui.Select):
    def __init__(self, bot, event_id, user_id, options):
        self.bot = bot
        self.event_id = event_id
        self.user_id = user_id
        super().__init__(
            placeholder="選擇要修改的題目（每題最多改一次）",
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

        await interaction.response.defer(ephemeral=True)
        await jio_cog.enter_edit_question_mode(self.event_id, self.user_id, self.values[0], interaction)


class ConfirmEditQuestionView(View):
    def __init__(self, bot, event_id, user_id, options):
        super().__init__(timeout=600)
        self.add_item(ConfirmEditQuestionSelect(bot, event_id, user_id, options))


class ConfirmSubmissionView(View):
    def __init__(self, bot, event_id, user_id):
        super().__init__(timeout=600)
        self.bot = bot
        self.event_id = event_id
        self.user_id = user_id

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的確認按鈕。", ephemeral=True)
            return

        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        self.clear_items()
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        await jio_cog.confirm_submission_from_button(self.event_id, self.user_id, interaction)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.blurple)
    async def edit_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的修改按鈕。", ephemeral=True)
            return

        jio_cog = self.bot.get_cog("Jio")
        if not jio_cog:
            await interaction.response.send_message("系統忙碌中，請稍後再試。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await jio_cog.open_edit_question_selector(self.event_id, self.user_id, interaction)



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

        # Inject helper method to Jio instance if needed, or just make it static/mixin.
        # But 'build_context_string' is on View above. Ideally it should be on Cog or helper.
        # I'll duplicate it or move it. For now, let's put it on Cog and call it from View.

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

    async def disable_management_view(self, event_id):
        db = self.bot.get_cog("Database")
        if not db:
            return

        event = await db.get_event(event_id)
        if not event:
            return

        channel = await self._resolve_channel(event.get("channel_id"))
        if not channel:
            return

        message_id = event.get("message_id")
        if not message_id:
            return

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(view=None)
        except Exception:
            pass

    async def build_confirm_submission_view(self, event_id, user_id):
        return ConfirmSubmissionView(self.bot, event_id, user_id)

    async def confirm_submission_from_button(self, event_id, user_id, interaction: discord.Interaction):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id) if db else None
        if not event:
            await interaction.followup.send("找不到活動，請稍後再試。", ephemeral=True)
            return

        await db.update_participant_interview(
            event_id,
            user_id,
            current_question_id="completed",
            interview_completed=True,
            confirmed=True,
        )
        await db.update_participant_status(event_id, user_id, "READY")
        await db.append_history(event_id, user_id, "system", "Participant confirmed final submission.")

        try:
            await interaction.user.send("✅ 已確認送出你的最終訪談結果。")
        except Exception:
            pass

        await self.maybe_trigger_adjudication(event_id)
        await self.update_dashboard(event_id)
        await self.log_event_state(event_id)
        await interaction.followup.send("✅ 已確認送出。", ephemeral=True)

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
        edited_ids = set(interview.get("edited_question_ids", []) or [])

        options = []
        for idx, question in enumerate(event.get("interview_questions", []) or [], start=1):
            qid = str(question.get("id") or "")
            if not qid or qid in edited_ids:
                continue
            options.append(
                discord.SelectOption(
                    label=f"{idx}. {str(question.get('text') or '')[:90]}",
                    value=qid,
                    description="選擇後請直接回覆新答案",
                )
            )

        if not options:
            await interaction.followup.send("你已用完可修改題目（每題最多 1 次）。", ephemeral=True)
            return

        view = ConfirmEditQuestionView(self.bot, event_id, user_id, options)
        await interaction.followup.send("請選擇要修改的題目。", view=view, ephemeral=True)

    async def enter_edit_question_mode(self, event_id, user_id, question_id, interaction: discord.Interaction):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id) if db else None
        participant = await db.get_participant(event_id, user_id) if db else None
        if not event or not participant:
            await interaction.followup.send("找不到面試資料。", ephemeral=True)
            return

        interview = participant.get("interview", {}) or {}
        edited_ids = list(interview.get("edited_question_ids", []) or [])
        if question_id in edited_ids:
            await interaction.followup.send("這一題已修改過，無法再次修改。", ephemeral=True)
            return

        edited_ids.append(question_id)
        await db.update_participant_interview(
            event_id,
            user_id,
            current_question_id=question_id,
            interview_completed=False,
            confirmed=False,
            edited_question_ids=edited_ids,
        )
        await db.update_participant_status(event_id, user_id, "INTERVIEWING")
        await db.set_participant_reply_status(event_id, user_id, "WAITING_FOR_REPLY")

        question_text = ""
        for question in event.get("interview_questions", []) or []:
            if str(question.get("id")) == str(question_id):
                question_text = str(question.get("text") or "")
                break

        remain = self._remaining_interview_minutes(event)
        tip = f"\n⏳ 面試剩餘時間：約 {remain} 分鐘" if remain is not None else ""
        await interaction.followup.send(f"請修改這一題答案：{question_text}{tip}", ephemeral=True)

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
            "請選擇要移出的 ON_HOLD 參與者，並填寫原因。",
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

        eligible_status = {"INTERVIEWING", "PENDING", "JOINED"}
        interview_questions = event.get("interview_questions", []) or []
        first_question = interview_questions[0] if interview_questions else None
        initiator_id = event.get("initiator_id")

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

            conflict = await db.find_conflicting_interview_event(uid, exclude_event_id=event_id)
            if conflict:
                await db.update_participant_status(event_id, uid, "KICKED")
                await db.events.update_one(
                    {"_id": event_id, "participants.user_id": uid},
                    {
                        "$set": {
                            "participants.$.review_status": "CONFLICT_ACTIVE_INTERVIEW",
                            "participants.$.hold_context": {
                                "verdict": "KICK",
                                "reason": f"Active interview conflict with {conflict.get('title', '未命名活動')}",
                            },
                        }
                    },
                )
                continue

            targets.append(uid)

            try:
                user = await self.bot.fetch_user(uid)
                await user.send(embed=base_embed)
                # Ensure participant state enters interview flow even if previous update missed.
                if first_question:
                    await db.update_participant_interview(
                        event_id,
                        uid,
                        current_question_id=first_question.get("id"),
                        interview_completed=False,
                        confirmed=False,
                    )
                    await db.update_participant_status(event_id, uid, "INTERVIEWING")
                    await db.set_participant_reply_status(event_id, uid, "WAITING_FOR_REPLY")
                    remaining = self._remaining_interview_minutes(event)
                    remain_tip = f"\n⏳ 面試剩餘時間：約 {remaining} 分鐘" if remaining is not None else ""
                    await user.send(f"➡️ 第 1 題：{first_question.get('text', '')}{remain_tip}")
                else:
                    await db.update_participant_interview(
                        event_id,
                        uid,
                        current_question_id="completed",
                        interview_completed=True,
                        confirmed=True,
                    )
                    await db.update_participant_status(event_id, uid, "READY")
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
            await db.append_history(event_id, None, "model", f"Interview started. First question: {first_text}", targets=prompts)
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
        cursor = db.events.find(
            {
                "cancelled": {"$ne": True},
                "workflow_state": {"$nin": ["CANCELLED", "FAILED_MIN_PARTICIPANTS"]},
                "participants.user_id": user_id,
                "participants": {
                    "$elemMatch": {
                        "user_id": user_id,
                        "status": {"$in": ["INTERVIEWING", "ON_HOLD"]}
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

        active_event_id = await db.get_user_active_event(user_id)

        event_by_id = {str(event["_id"]): event for event in events}

        if active_event_id and active_event_id in event_by_id:
            return event_by_id[active_event_id]

        selected_event = events[0]
        await db.set_user_active_event(user_id, str(selected_event["_id"]))
        return selected_event

    def build_nsw_candidates(self, participants):
        users = []
        what_options = set()
        where_options = set()
        when_options = set()
        how_options = set()

        for participant in participants:
            if participant.get("status") in ["KICKED", "DECLINED", "FINISHED"]:
                continue

            preferences = (participant.get("interview", {}) or {}).get("answers", {}) or {}
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

        await self.send_adjudication_report(event_id)

    async def send_adjudication_report(self, event_id):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id)
        if not event:
            return

        candidates = self.build_nsw_candidates(event.get("participants", []))
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
        await db.set_user_active_event(initiator_id, str(event_id))

        user = self.bot.get_user(initiator_id)
        if not user:
            try:
                user = await self.bot.fetch_user(initiator_id)
            except Exception:
                user = None

        if not user:
            return

        report_lines = [
            f"📋 揪霸 (Jio Ba) 給主揪的幕僚報告 - {event.get('title', '未命名活動')}",
            ""
        ]

        questions = event.get("interview_questions", []) or []
        for participant in event.get("participants", []):
            if participant.get("status") in ["DECLINED", "FINISHED"]:
                continue

            uid = participant.get("user_id")
            user_obj = self.bot.get_user(uid)
            name = user_obj.display_name if user_obj else f"User {uid}"
            status = participant.get("status", "UNKNOWN")
            answers = (participant.get("interview", {}) or {}).get("answers", {}) or {}
            notes = participant.get("notes", {}) or {}

            report_lines.append(f"👤 {name} ({status})")
            for idx, question in enumerate(questions, start=1):
                qid = question.get("id")
                qtext = question.get("text", "")
                avalue = str(answers.get(qid) or "(未填)")
                report_lines.append(f"{idx}. {qtext}")
                report_lines.append(f"   ↳ {avalue}")

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

        view = AdjudicationView(self.bot, event_id)
        await user.send("\n".join(report_lines) + "\n\n請由你做最終裁決。", view=view)

    async def apply_adjudication_choice(self, event_id, user_id, choice_index, interaction=None):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id)
        if not event:
            if interaction:
                await interaction.response.send_message("活動不存在。", ephemeral=True)
            return

        if event.get("initiator_id") != user_id:
            if interaction:
                await interaction.response.send_message("只有發起人可裁決。", ephemeral=True)
            return

        candidates = event.get("adjudication_candidates", []) or []
        idx = choice_index - 1
        if idx < 0 or idx >= len(candidates):
            if interaction:
                await interaction.response.send_message("方案不存在。", ephemeral=True)
            return

        selected = candidates[idx]
        scheduled_event_id = await self.create_scheduled_event_from_plan(event, selected)

        await db.events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "adjudication_status": "DECIDED",
                    "adjudication_result": selected,
                    "adjudication_by": user_id,
                    "adjudication_at": datetime.datetime.utcnow(),
                    "scheduled_event_id": str(scheduled_event_id) if scheduled_event_id else None,
                }
            }
        )

        await self.disable_management_view(event_id)

        channel = await self._resolve_channel(event.get("channel_id"))
        if channel:
            candidate = selected.get("candidate", {})
            ready_mentions = []
            for p in event.get("participants", []):
                if p.get("status") == "READY":
                    ready_mentions.append(f"<@{p.get('user_id')}>")
            announce_text = (
                f"📢 **揪霸 (Jio Ba) 最終方案出爐!**\n"
                f"{candidate.get('what')}｜{candidate.get('where')}｜{candidate.get('when')}｜執行方式 {candidate.get('budget')}"
            )
            if scheduled_event_id:
                announce_text += f"\n已建立 Discord Event (ID: {scheduled_event_id})"
                if ready_mentions:
                    announce_text += "\n請以下通過面試的成員前往活動事件按 Interested：\n" + " ".join(ready_mentions)
            await channel.send(announce_text)

        if interaction:
            await interaction.response.send_message("✅ 已完成裁決並公告。", ephemeral=True)

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
        if not event: return

        if event.get("cancelled"):
            return

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
            return
        
        if event.get("active") is False and not manual_trigger_user:
            return
            
        # 1. Mark Inactive (No more joining)
        await db.events.update_one({"_id": event_id}, {"$set": {"active": False}})
        
        # 2. Notify Channel
        event_channel = await self._resolve_channel(event.get("channel_id"))
        if event_channel:
            msg = f"⏰ 報名截止！開始面試..."
            if manual_trigger_user:
                msg = f"⚡ {manual_trigger_user.mention} 提前截止了報名！開始面試..."
            try:
                await event_channel.send(msg)
            except:
                pass

        # 2.5 Update PENDING users to INTERVIEWING
        # We need to do this so on_message can detect them
        # And also so the Agent knows they are active targets
        await db.events.update_one(
            {"_id": event_id, "participants.status": "PENDING"},
            {"$set": {"participants.$[elem].status": "INTERVIEWING"}},
            array_filters=[{"elem.status": "PENDING"}]
        )
        
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
            desc_text += f"\n\n**4W1H Seeds**\n{seed_text}"

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
            elif status == "READY":
                text_ready.append(line)
            elif status == "KICKED":
                text_kicked.append(line)
            elif status == "ON_HOLD":
                text_on_hold.append(line)
        
        if text_joined: embed.add_field(name="剛加入 (Joined)", value="\n".join(text_joined), inline=False)
        if text_interviewing: embed.add_field(name="面試中 (Interviewing)", value="\n".join(text_interviewing), inline=False)
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
        if seconds > 0:
            await asyncio.sleep(seconds)

        db = self.bot.get_cog("Database")
        if not db:
            return

        kicked_ids = await db.auto_kick_interview_overdue(event_id)
        event = await db.get_event(event_id)
        if not event:
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

    @discord.slash_command(description="匯出活動狀態 JSON")
    async def export_status(self, ctx, event_id: str = discord.Option(str, "活動 ID")):
        import json
        from bson import ObjectId
        
        db = self.bot.get_cog("Database")
        try:
            # Validate ObjectId
            if not ObjectId.is_valid(event_id):
                await ctx.respond("無效的 Event ID。", ephemeral=True)
                return

            event = await db.get_event(ObjectId(event_id))
            if not event:
                await ctx.respond("找不活動。", ephemeral=True)
                return

            # Prepare Export Data
            # Convert ObjectId to str for JSON serialization
            def json_serial(obj):
                if isinstance(obj, ObjectId):
                    return str(obj)
                raise TypeError ("Type %s not serializable" % type(obj))

            # Construct a "Question Table" view
            # i.e., Participant Name | Status | Interview Summary
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
                    "display_name": user.display_name if user else "Unknown",
                    "status": p["status"],
                    "answers": (p.get("interview", {}) or {}).get("answers", {}),
                    "warning_count": p.get("warning_count", 0),
                    # We can also infer "Pending Questions" if we had a structured way.
                    # For now just dump what we have.
                }
                export_data["participants"].append(p_data)

            file_content = json.dumps(export_data, indent=2, default=json_serial, ensure_ascii=False)
            
            # Save to temporary file and send
            filename = f"status_{event_id}.json"
            with open(filename, "w", encoding='utf-8') as f:
                f.write(file_content)
                
            await ctx.respond(f"📊 活動狀態匯出: `{event.get('description')}`", file=discord.File(filename))
            os.remove(filename)

        except Exception as e:
            await ctx.respond(f"匯出失敗: {e}", ephemeral=True)

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
                return

            event = resolved
            event_id = event["_id"]
            print(f"[DEBUG LOG] Locked onto Event ID: {event_id} for user {message.author.name}")

            if event.get("cancelled") or event.get("workflow_state") in {"CANCELLED", "FAILED_MIN_PARTICIPANTS"}:
                await message.author.send("此活動已取消，訪談已停止。")
                return

            participant = await db.get_participant(event_id, message.author.id)
            if participant and participant.get("status") == "ON_HOLD":
                await message.author.send("目前你的訪談狀態為 ON_HOLD，請等待主揪裁決後再繼續。")
                return
            
            # Log User Msg
            await db.append_history(event_id, message.author.id, "user", message.content, author_name=message.author.display_name)
            await db.set_participant_reply_status(event_id, message.author.id, "REPLIED")
            
            # Prepare State
            # Prepare State
            # OLD LOGIC MOVED TO AIBRAIN TO PREVENT RACE CONDITIONS
            
            user_inputs = {message.author.id: message.content}

            # Invoke Agent (Via Queue)
            if brain:
                print(f"[DEBUG LOG] Queueing message for AIBrain... (Event {event_id})")
                try:
                    await brain.queue_message(str(event_id), message.author.id, message.content, message.author.display_name)
                    await self.maybe_trigger_adjudication(event_id)
                    # We don't wait for result here. It runs in background.
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
                    # [MODIFIED] History is now global, so per-user count is less relevant or needs filter
                    # "history_count": len(p.get("conversation_history", [])) 
                }
                export_data["participants"].append(p_data)
            
            # [NEW] Add global history to export
            export_data["conversation_history_count"] = len(event.get("conversation_history", []))
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
    
