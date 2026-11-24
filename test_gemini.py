import os
from google import genai

api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    raise RuntimeError("GEMINI_API_KEY is not configured.")

client = genai.Client(api_key=api_key)

print("\nTesting Gemini API...\n")

response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="""
    You are testing the CiviSense civic complaint assistant.
    Reply in two short parts:
    1. Introduce yourself in English.
    2. Say hello in Kannada.
    """
)

print("SUCCESS! Gemini API is responding.")
print("\nGemini response:")
print(response.text)
