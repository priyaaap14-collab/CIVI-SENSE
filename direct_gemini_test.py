import os
import requests

key = os.environ.get("GEMINI_API_KEY")

if not key:
    raise RuntimeError("Gemini API key is missing.")

url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"

headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": key
}

payload = {
    "contents": [{
        "parts": [{"text": "Reply with: CiviSense API connection successful."}]
    }]
}

print("Testing direct Gemini API connection...")

try:
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=90
    )

    print("HTTP Status:", response.status_code)
    print("Content-Type:", response.headers.get("Content-Type"))

    try:
        data = response.json()
        if response.ok:
            print("SUCCESS!")
            print(data["candidates"][0]["content"]["parts"][0]["text"])
        else:
            print("API ERROR:")
            print(data)
    except ValueError:
        print("Non-JSON response:")
        print(response.text[:500])

except requests.RequestException as e:
    print("CONNECTION ERROR:", type(e).__name__)
    print(str(e)[:500])

