import os

with open('cogs/jio.py', 'r', encoding='utf-8') as f:
    jio_content = f.read()

old_end_interview = '''    async def end_interview(self, button: discord.ui.Button, interaction: discord.Interaction):
        jio_cog = self.bot.get_cog("Jio")'''

new_end_interview = '''    async def end_interview(self, button: discord.ui.Button, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        
        jio_cog = self.bot.get_cog("Jio")'''

jio_content = jio_content.replace(old_end_interview, new_end_interview)

# Now wait, I disabled the button with edit_message, but later it tries to defer and follow up.
jio_content = jio_content.replace('await interaction.response.defer(ephemeral=True)', '# removed defer because edit_message handles it')

with open('cogs/jio.py', 'w', encoding='utf-8') as f:
    f.write(jio_content)

print('jio.py fully ok')
