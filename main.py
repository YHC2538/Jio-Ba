import discord
import os
import asyncio
import sys
from discord.ext import commands
from dotenv import load_dotenv
import warnings

# Suppress LangChain UserWarning about transport
warnings.filterwarnings("ignore", message=".*transport is not default parameter.*")

load_dotenv()

if not hasattr(discord, "Bot"):
    print("🛑 CRITICAL ERROR: Unsupported discord package detected (missing discord.Bot).")
    print("This project requires py-cord in the project virtual environment.")
    print("Run with: D:/Jio-Ba/.venv/Scripts/python.exe D:/Jio-Ba/Wei-Jia-Ba/main.py")
    sys.exit(1)

# Startup Environment Check
required_vars = ["GOOGLE_API_KEY", "GOOGLE_API_ENDPOINT"]
missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    print(f"🛑 CRITICAL ERROR: Missing required environment variables: {', '.join(missing_vars)}")
    exit(1)

# Check for limited mode flag
LIMITED_MODE_FILE = ".limited_mode"
use_limited_mode = os.path.exists(LIMITED_MODE_FILE)

intents = discord.Intents.default()
if use_limited_mode:
    print("⚠️  Running in LIMITED MODE (No AI) due to missing privileges.")
    intents.message_content = False
else:
    intents.message_content = True

# Using discord.Bot for Slash Commands only
bot = discord.Bot(intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    if not bot.intents.message_content and not use_limited_mode:
        print("⚠️  WARNING: Message Content Intent is DISABLED. AI features will NOT work.")

if __name__ == "__main__":
    # Load Cogs
    cogs = ['cogs.db', 'cogs.ai_brain', 'cogs.jio']
    for cog in cogs:
        try:
            bot.load_extension(cog)
            print(f"Loaded extension: {cog}")
        except Exception as e:
            print(f"Failed to load extension {cog}: {e}")

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found in .env")
    else:
        try:
            bot.run(token)
        except discord.errors.PrivilegedIntentsRequired:
            print("\n" + "="*60)
            print("🛑 CRITICAL ERROR: Privileged Intents Required")
            print("="*60)
            print("Creating lock file and exiting to trigger restart in LIMITED MODE...")
            
            with open(LIMITED_MODE_FILE, "w") as f:
                f.write("1")
            
            # Exit to let PM2 restart us
            import sys
            sys.exit(1)

