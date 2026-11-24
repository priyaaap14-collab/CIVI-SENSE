import os
from pathlib import Path
from dotenv import load_dotenv
from google import genai

load_dotenv(dotenv_path=Path(".env"))

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY was not loaded")

model = os.getenv("CIVISENSE_GEMINI_MODEL", "gemini-3.5-flash-lite")

print("Testing Gemini model:", model)

client = genai.Client(api_key=api_key)

response = client.models.generate_content(
    model=model,
    contents="Reply with exactly: GEMINI CONNECTION OK"
)

print("Gemini response:", response.text)
print("REAL API TEST PASSED")
