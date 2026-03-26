import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai


project_root = Path(__file__).resolve().parents[1]
load_dotenv(project_root / ".env")

# Prefer project standard key name, but keep compatibility with GEMINI_API_KEY.
api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("Missing API key. Set GOOGLE_API_KEY (or GEMINI_API_KEY) in .env.")

client = genai.Client(api_key=api_key)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Explain why puppus are cute in 3 sentences.",
)
print(response.text)