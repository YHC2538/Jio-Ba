import discord
from discord.ext import commands
from discord.ui import View, Button
import asyncio
import json
import os

from bson import ObjectId

class JioCreationModal(discord.ui.Modal):
    def __init__(self, bot, chan_id, default_title=None, default_time=None, default_decision_mode=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.channel_id = chan_id
        
        self.add_item(discord.ui.InputText(
            label="聚餐標題",
            placeholder="例如：週五小酌、加班晚餐",
            style=discord.InputTextStyle.short,
            required=True,
            value=default_title
        ))
        
        self.add_item(discord.ui.InputText(
            label="聚餐說明 (時間/地點/備註)",
            placeholder="沒填就是讓大家討論決定",
            style=discord.InputTextStyle.long,
            required=False
        ))
        
        self.add_item(discord.ui.InputText(
            label="等待時間 (分鐘)",
            placeholder="例如：30 (留空則不設自動截止)",
            style=discord.InputTextStyle.short,
            required=False,
            value=str(default_time) if default_time else None
        ))
        
        # Decision Mode Input
        decision_val = None
        if default_decision_mode == "RANDOM": decision_val = "1"
        elif default_decision_mode == "DICTATOR": decision_val = "2"
        elif default_decision_mode == "AI": decision_val = "3"
        
        self.add_item(discord.ui.InputText(
            label="決策模式 (1.隨機 2.獨裁 3.AI)",
            placeholder="請輸入 1, 2 或 3 (預設 AI)",
            style=discord.InputTextStyle.short,
            required=False,
            value=decision_val
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        title = self.children[0].value
        description = self.children[1].value
        time_input = self.children[2].value
        decision_input = self.children[3].value
        
        # Validations
        time_limit = None
        if time_input:
            try:
                time_limit = int(time_input)
            except ValueError:
                await interaction.followup.send("❌ 等待時間必須是數字！", ephemeral=True)
                return

        # Decision Mode Logic
        decision_mode = "AI" # Default
        if decision_input:
            d = decision_input.lower().strip()
            if d in ["1", "隨機", "random", "骰子"]:
                decision_mode = "RANDOM"
            elif d in ["2", "獨裁", "dictator", "host", "皇帝"]:
                decision_mode = "DICTATOR"
        
        # Prepare description
        final_description = description if description else "（由大家討論決定）"

        db = self.bot.get_cog("Database")
        
        # 1. Create event
        event_id = await db.create_event(
            initiator_id=interaction.user.id,
            description=final_description, # DB description field
            channel_id=self.channel_id,
            title=title,
            decision_mode=decision_mode
        )
        
        # Update extra settings (Time limit)
        if time_limit:
            import datetime
            deadline = datetime.datetime.utcnow() + datetime.timedelta(minutes=time_limit)
            await db.events.update_one({"_id": event_id}, {"$set": {"deadline": deadline}})
        
        # 2. Prepare Embed
        embed_title = f"🍱 聚餐: {title}"
        mode_text = "🤖 AI 決定"
        if decision_mode == "RANDOM": mode_text = "🎲 隨機抽籤"
        elif decision_mode == "DICTATOR": mode_text = "👑 獨裁者 ({})".format(interaction.user.display_name)
        
        embed_desc = f"{final_description}\n\n**決策模式**: {mode_text}\n\n點擊下方按鈕加入！"
        
        if time_limit:
            import time
            future_time = int(time.time() + (time_limit * 60))
            embed_desc += f"\n\n⏳ **截止時間**: <t:{future_time}:R>"
        
        embed = discord.Embed(title=embed_title, description=embed_desc, color=0x00ff00)
        embed.set_footer(text=f"發起人: {interaction.user.display_name}")
        
        view = JoinView(self.bot, event_id)
        
        # 3. Send Message
        msg = await interaction.followup.send(embed=embed, view=view)
        await db.update_event_message_id(event_id, msg.id)
        
        # Handle Time Limit Task
        if time_limit:
            jio_cog = self.bot.get_cog("Jio")
            if jio_cog:
                self.bot.loop.create_task(jio_cog.close_event_after(event_id, time_limit * 60))



class JioEditModal(discord.ui.Modal):
    def __init__(self, bot, event_id, current_title, current_desc, current_time, current_mode, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.event_id = event_id
        
        self.add_item(discord.ui.InputText(
            label="聚餐標題",
            value=current_title,
            style=discord.InputTextStyle.short,
            required=True
        ))
        
        self.add_item(discord.ui.InputText(
            label="聚餐說明",
            value=current_desc,
            style=discord.InputTextStyle.long,
            required=False
        ))
        
        self.add_item(discord.ui.InputText(
            label="等待時間 (分鐘) - 修改將重置截止時間",
            value=str(current_time) if current_time else "",
            placeholder="留空則不變更 (若原無截止則保持無)",
            style=discord.InputTextStyle.short,
            required=False
        ))
        
        mode_val = "3"
        if current_mode == "RANDOM": mode_val = "1"
        elif current_mode == "DICTATOR": mode_val = "2"
        
        self.add_item(discord.ui.InputText(
            label="決策模式 (1.隨機 2.獨裁 3.AI)",
            value=mode_val,
            style=discord.InputTextStyle.short,
            required=False
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        new_title = self.children[0].value
        new_desc = self.children[1].value
        time_input = self.children[2].value
        mode_input = self.children[3].value
        
        db = self.bot.get_cog("Database")
        
        # Parse decision mode
        decision_mode = "AI"
        if mode_input:
            d = mode_input.lower().strip()
            if d in ["1", "隨機", "random", "骰子"]: decision_mode = "RANDOM"
            elif d in ["2", "獨裁", "dictator", "host", "皇帝"]: decision_mode = "DICTATOR"
            
        update_data = {
            "title": new_title,
            "description": new_desc if new_desc else "（由大家討論決定）",
            "decision_mode": decision_mode
        }
        
        # Handle time limit
        if time_input:
            try:
                minutes = int(time_input)
                import datetime
                new_deadline = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
                update_data["deadline"] = new_deadline
                
                # Update task
                jio_cog = self.bot.get_cog("Jio")
                if jio_cog:
                    self.bot.loop.create_task(jio_cog.close_event_after(self.event_id, minutes * 60))
                    
            except ValueError:
                await interaction.followup.send("❌ 時間格式錯誤，僅已更新其他設定。", ephemeral=True)
        
        await db.events.update_one({"_id": self.event_id}, {"$set": update_data})
        
        # Update Dashboard
        jio_cog = self.bot.get_cog("Jio")
        if jio_cog:
            await jio_cog.update_dashboard(self.event_id)
            
        await interaction.followup.send("✅ 設定已更新！", ephemeral=True)


class ManageSelect(discord.ui.Select):
    def __init__(self, bot, event_id):
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
                description="[發起人] 修改標題、說明或決策模式",
                emoji="⚙️"
            ),
             discord.SelectOption(
                label="編輯回覆 (Edit Reply)", 
                value="EDIT_REPLY", 
                description="[參加者] 修改您的參加偏好",
                emoji="📝"
            )
        ]
        
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
            
            jio_cog = self.bot.get_cog("Jio")
            if jio_cog:
                # Defer first
                await interaction.response.defer(ephemeral=True)
                await jio_cog.start_interview_phase(self.event_id, manual_trigger_user=interaction.user)
                await interaction.followup.send("✅ 已提前截止並開始面試！", ephemeral=True)
                
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
                current_time=None, # Hard to calc remaining time, user sets NEW time
                current_mode=event.get("decision_mode", "AI"),
                title="編輯活動設定"
            )
            await interaction.response.send_modal(modal)

        elif value == "EDIT_REPLY":
            # Participant Only
            participant = None
            for p in event.get("participants", []):
                if p["user_id"] == user_id:
                    participant = p
                    break
            
            if not participant:
                await interaction.response.send_message("⛔ 您尚未參加活動，無法編輯回覆。", ephemeral=True)
                return
            
            # Form existing prefs
            existing_prefs = ""
            if participant.get("is_whatever"):
                existing_prefs = "隨便"
            elif participant.get("constraints"):
                existing_prefs = ", ".join(participant["constraints"])

            # Show Preference Modal (Reuse)
            modal = PreferenceModal(
                self.bot, 
                self.event_id, 
                title="修改參加偏好", 
                default_preference=existing_prefs
            )
            await interaction.response.send_modal(modal)


class JoinView(View):
    def __init__(self, bot, event_id):
        super().__init__(timeout=None) # Persistent view
        self.bot = bot
        self.event_id = event_id
        self.add_item(ManageSelect(bot, event_id))


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
                 await interaction.response.send_message("✅ 您已經報名過了！\n如需修改內容，請使用下方的「管理選單」->「編輯回覆」。", ephemeral=True)
                 return

        # Open Modal (Preference Collection)
        modal = PreferenceModal(self.bot, self.event_id, title="參加聚餐")
        await interaction.response.send_modal(modal)

class PreferenceModal(discord.ui.Modal):
    def __init__(self, bot, event_id, default_preference=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.event_id = event_id
        
        self.add_item(discord.ui.InputText(
            label="你想吃什麼？",
            placeholder="例如：日式、火鍋、不要辣... (隨便也可以填隨便)",
            style=discord.InputTextStyle.long,
            required=True,
            value=default_preference
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        preference = self.children[0].value
        db = self.bot.get_cog("Database")
        
        await db.add_participant(self.event_id, interaction.user.id)
        await db.append_history(self.event_id, interaction.user.id, "user", preference, author_name=interaction.user.display_name)
        await db.update_participant_status(self.event_id, interaction.user.id, "PENDING")
        
        jio_cog = self.bot.get_cog("Jio")
        if jio_cog:
            await jio_cog.update_dashboard(self.event_id)
            await jio_cog.log_event_state(self.event_id)
            
        await interaction.followup.send(f"✅ 已加入！您的偏好 ({preference}) 已紀錄。\n⏳ 請等待活動報名截止後，Bot 會私訊您開始點餐面試。", ephemeral=True)



class Jio(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Inject helper method to Jio instance if needed, or just make it static/mixin.
        # But 'build_context_string' is on View above. Ideally it should be on Cog or helper.
        # I'll duplicate it or move it. For now, let's put it on Cog and call it from View.
    
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
            
            c_list = p.get("constraints", [])
            is_whatever = p.get("is_whatever", False)
            
            status_desc = ""
            if is_whatever: status_desc = " (Whatever)"
            elif c_list: status_desc = f": {', '.join(c_list)}"
            else: status_desc = " (No specific constraints yet)"
            
            summary_lines.append(f"- {name}{status_desc}")
            
        return "\n".join(summary_lines)

    async def start_interview_phase(self, event_id, manual_trigger_user=None):
        db = self.bot.get_cog("Database")
        event = await db.get_event(event_id)
        if not event: return
        
        if event.get("active") is False and not manual_trigger_user:
            return
            
        # 1. Mark Inactive (No more joining)
        await db.events.update_one({"_id": event_id}, {"$set": {"active": False}})
        
        # 2. Notify Channel
        channel = self.bot.get_channel(event["channel_id"])
        if channel:
            msg = f"⏰ 報名截止！開始面試..."
            if manual_trigger_user:
                msg = f"⚡ {manual_trigger_user.mention} 提前截止了報名！開始面試..."
            try:
                await channel.send(msg)
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
            msg = await channel.fetch_message(event["message_id"])
            if msg:
                # Remove View
                await msg.edit(view=None)
        except Exception as e:
            print(f"[WARN] Failed to remove join button: {e}")
        
        # Refresh event from DB to get updated statuses
        event = await db.get_event(event_id)

        # 3. Formulate State for Agent
        participants_dict = {}
        for p in event.get("participants", []):
            uid = p["user_id"]
            # Try to get server nickname
            channel = self.bot.get_channel(event.get("channel_id"))
            member = None
            if channel and hasattr(channel, 'guild') and channel.guild:
                member = channel.guild.get_member(uid)
            
            user = member or self.bot.get_user(uid)
            name = user.display_name if user else f"User{uid}"
            participants_dict[uid] = {
                "id": uid,
                "name": name,
                "constraints": p.get("constraints", []),
                "status": p.get("status"),
                "is_whatever": p.get("is_whatever", False),
                "history": p.get("conversation_history", [])
            }

        # 4. Invoke Agent (Coordinator)
        # We pass empty inputs because this is the start phase (Coordinator initiates)
        # State fetching is now inside invoke_agent
        brain = self.bot.get_cog("AIBrain")
        if brain:
            await brain.invoke_agent(str(event_id), {})

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

        channel = self.bot.get_channel(event["channel_id"])
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

        title_text = event.get("title", "未命名聚餐")
        desc_text = event.get("description", "（由大家討論決定）")
        
        # [FIX] Restore Decision Mode and Deadline display
        decision_mode = event.get("decision_mode", "AI")
        mode_text = "🤖 AI 決定"
        if decision_mode == "RANDOM": mode_text = "🎲 隨機抽籤"
        elif decision_mode == "DICTATOR": 
            initiator_id = event.get("initiator_id")
            initiator_name = "Unknown"
            # Try to fetch initiator name
            if channel.guild:
                mem = channel.guild.get_member(initiator_id)
                if mem: initiator_name = mem.display_name
            mode_text = f"👑 獨裁者 ({initiator_name})"
        
        desc_text += f"\n\n**決策模式**: {mode_text}\n\n點擊下方按鈕加入！"
        
        deadline = event.get("deadline")
        if deadline:
            import datetime
            # Ensure deadline is aware or naive consistently. Mongo returns datetime.
            # Convert to unix timestamp for discord
            ts = int(deadline.replace(tzinfo=datetime.timezone.utc).timestamp())
            desc_text += f"\n\n⏳ **截止時間**: <t:{ts}:R>"

        # Find initiator for footer
        initiator_id = event.get("initiator_id")
        initiator_name = "Unknown"
        if channel.guild:
             mem = channel.guild.get_member(initiator_id)
             if mem: initiator_name = mem.display_name

        embed = discord.Embed(title=f"🍱 聚餐: {title_text}", description=desc_text, color=0xffa500)
        embed.set_footer(text=f"發起人: {initiator_name}")
        
        # Build participants list
        participants = event.get("participants", [])
        
        text_joined = []
        text_interviewing = []
        text_ready = []
        text_kicked = []
        
        for p in participants:
            user = self.bot.get_user(p["user_id"])
            if not user and channel.guild:
                user = channel.guild.get_member(p["user_id"])
                
            name = user.display_name if user else f"User {p['user_id']}"
            status = p["status"]
            
            line = f"{name}"
            if p.get("is_whatever"):
                line += " (⚠️隨便黨)"
            
            if status == "PENDING":
                text_joined.append(line)
            elif status == "INTERVIEWING":
                text_interviewing.append(line)
            elif status == "READY":
                text_ready.append(line)
            elif status == "KICKED":
                text_kicked.append(line)
        
        if text_joined: embed.add_field(name="剛加入 (Joined)", value="\n".join(text_joined), inline=False)
        if text_interviewing: embed.add_field(name="面試中 (Interviewing)", value="\n".join(text_interviewing), inline=False)
        if text_ready: embed.add_field(name="準備好了 (Ready)", value="\n".join(text_ready), inline=False)
        if text_kicked: embed.add_field(name="已踢出 (Kicked)", value="\n".join(text_kicked), inline=False)
        
        if not participants:
             embed.add_field(name="參加者", value="(尚無人參加)", inline=False)
        
        try:
            await msg.edit(embed=embed)
            print("[DEBUG] Message edited successfully")
        except Exception as e:
            print(f"[DEBUG] Failed to edit message: {e}")

    @discord.slash_command(description="發起一個聚餐活動")
    async def jio(
        self, 
        ctx, 
        title: str = discord.Option(str, "聚餐標題 (選填，將預填入表單)", default=None, required=False),
        time_limit: int = discord.Option(int, "等待時間 (選填，將預填入表單)", default=None, required=False),
        decision_mode: str = discord.Option(
            str, 
            "決策模式 (選填)", 
            default=None, 
            required=False,
            choices=[
                discord.OptionChoice("🎲 隨機抽籤 (Random)", "RANDOM"),
                discord.OptionChoice("👑 獨裁者 (Dictator)", "DICTATOR"),
                discord.OptionChoice("🤖 AI 決定 (Default)", "AI")
            ]
        )
    ):
        modal = JioCreationModal(
            self.bot, 
            ctx.channel.id, 
            title="發起聚餐",
            default_title=title, 
            default_time=time_limit,
            default_decision_mode=decision_mode
        )
        await ctx.send_modal(modal)


    async def close_event_after(self, event_id, seconds):
        await asyncio.sleep(seconds)
        # Auto-start interview
        jio_cog = self.bot.get_cog("Jio") 
        if jio_cog:
           await jio_cog.start_interview_phase(event_id)

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
            # i.e., Participant Name | Status | Constraints | History Summary
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
                    "constraints": p["constraints"],
                    "is_whatever": p.get("is_whatever", False),
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
            
            # Find active event for this user
            print(f"[DEBUG LOG] Searching for LATEST event for user {message.author.id}...")
            # [FIX] User Request: "Assume only one event, read latest". 
            # We sort by _id descending to get the newest event this user is involved in.
            cursor = db.events.find({"participants.user_id": message.author.id}).sort("_id", -1)
            active_event = await cursor.to_list(length=1)
            
            if not active_event:
                print(f"[DEBUG LOG] No event found for user {message.author.name}.")
                return
                
            # Use the latest event found
            event = active_event[0]
            event_id = event["_id"]
            print(f"[DEBUG LOG] Locked onto LATEST Event ID: {event_id} for user {message.author.name}")
            
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
                    "constraints": p["constraints"],
                    "is_whatever": p.get("is_whatever", False),
                    # [MODIFIED] History is now global, so per-user count is less relevant or needs filter
                    # "history_count": len(p.get("conversation_history", [])) 
                }
                export_data["participants"].append(p_data)
            
            # [NEW] Add global history to export
            export_data["conversation_history_count"] = len(event.get("conversation_history", []))
            export_data["current_phase"] = event.get("current_phase", "LOGISTICS")

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
