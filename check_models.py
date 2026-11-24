import os
from google import genai
import google.genai

print("SDK version:", google.genai.__version__)

key = os.getenv("GEMINI_API_KEY")
if not key:
    raise RuntimeError("API key missing from this terminal.")

client = genai.Client(api_key=key)

print("\nChecking available models...\n")

try:
    models = client.models.list()
    for model in models:
        name = getattr(model, "name", "")
        actions = getattr(model, "supported_actions", [])
        if "generateContent" in actions:
            print(name)
except Exception as e:
    print("MODEL LIST ERROR:", type(e).__name__)
    print(str(e)[:1000])
