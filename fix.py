import os

with open('cogs/jio.py', 'r', encoding='utf-8') as f:
    jio_content = f.read()

# Fix Button Double Click
old_end_signup = '''    async def end_signup(self, button: discord.ui.Button, interaction: discord.Interaction):
        jio_cog = self.bot.get_cog("Jio")'''

new_end_signup = '''    async def end_signup(self, button: discord.ui.Button, interaction: discord.Interaction):
        # Disable buttons immediately to prevent double clicks
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        
        jio_cog = self.bot.get_cog("Jio")'''

old_defer = 'await interaction.response.defer(ephemeral=True)'
new_defer = '# await interaction.response.defer(ephemeral=True)'

jio_content = jio_content.replace(old_end_signup, new_end_signup)

with open('cogs/jio.py', 'w', encoding='utf-8') as f:
    f.write(jio_content)

print('jio.py ok')

with open('cogs/ai_brain.py', 'r', encoding='utf-8') as f:
    ai_content = f.read()

# Fix target_uid and debounce
ai_content = ai_content.replace('await asyncio.sleep(1.2)', 'await asyncio.sleep(0.5)')
ai_content = ai_content.replace('user_for_loading = self.bot.get_user(target_uid)', 'user_for_loading = self.bot.get_user(int(target_uid))')
ai_content = ai_content.replace('user = self.bot.get_user(target_uid)', 'user = self.bot.get_user(int(target_uid))')

with open('cogs/ai_brain.py', 'w', encoding='utf-8') as f:
    f.write(ai_content)

print('ai_brain.py ok')

