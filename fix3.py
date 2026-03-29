import os

with open('cogs/ai_brain.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('user_for_loading = self.bot.get_user(int(target_uid))', 'user_for_loading = self.bot.get_user(int(target_uid)) or await self.bot.fetch_user(int(target_uid))')
content = content.replace('user = self.bot.get_user(int(target_uid))', 'user = self.bot.get_user(int(target_uid)) or await self.bot.fetch_user(int(target_uid))')

with open('cogs/ai_brain.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('ai_brain.py fetch_user added')
