from google import genai
import os
from dotenv import load_dotenv
import google.auth

load_dotenv()
credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
client = genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                      location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"), credentials=credentials)

print("Available Models:")
for m in client.models.list():
    if 'generateContent' in (m.supported_actions or []):
        print(f"- {m.name}")
