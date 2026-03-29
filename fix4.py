import os

with open('cogs/ai_brain.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace user_for_loading 
target_1 = 'user_for_loading = self.bot.get_user(int(target_uid)) or await self.bot.fetch_user(int(target_uid))'
replacement_1 = '''try:
                        user_for_loading = self.bot.get_user(int(target_uid)) or await self.bot.fetch_user(int(target_uid))
                    except Exception as try_e:
                        print(f"[DEBUG LOG] Could not fetch user_for_loading: {try_e}")
                        user_for_loading = None'''
content = content.replace(target_1, replacement_1)

# Replace user
target_2 = 'user = self.bot.get_user(int(target_uid)) or await self.bot.fetch_user(int(target_uid))'
replacement_2 = '''try:
                                        user = self.bot.get_user(int(target_uid)) or await self.bot.fetch_user(int(target_uid))
                                    except Exception:
                                        user = None'''
content = content.replace(target_2, replacement_2)

with open('cogs/ai_brain.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('ai_brain protected')
