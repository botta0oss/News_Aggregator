import asyncio
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.config import settings
from backend.db.database import engine
from sqlalchemy import text

print(f"Groq API key configured: {bool(settings.GROQ_API_KEY and not settings.GROQ_API_KEY.startswith('your_'))}")
print(f"TypeSafe key configured: {bool(settings.TYPESAFE_API_KEY and not settings.TYPESAFE_API_KEY.startswith('your_'))}")
print(f"Gemini key configured: {bool(settings.GEMINI_API_KEY and not settings.GEMINI_API_KEY.startswith('your_'))}")

async def test_conn():
    try:
        async with engine.connect() as conn:
            res = await conn.execute(text("SELECT 1;"))
            print("Database connection OK:", res.scalar())
    except Exception as e:
        print("Database connection status:", type(e).__name__, ":", str(e)[:150])

asyncio.run(test_conn())
