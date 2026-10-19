from google import genai
import os
from dotenv import load_dotenv
import google.auth

load_dotenv()

project = os.getenv("GOOGLE_CLOUD_PROJECT")
if not project:
    print("❌ GOOGLE_CLOUD_PROJECT is not set")
    exit(1)

credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
client = genai.Client(vertexai=True, project=project,
                      location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"), credentials=credentials)

print("🔍 Listing Available Models...")
try:
    for m in client.models.list():
        if 'generateContent' in (m.supported_actions or []):
            print(f"✅ Found: {m.name}")
except Exception as e:
    print(f"❌ Error listing models: {e}")
