from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")

if api_key:
    print(f"Key loaded. Length: {len(api_key)} characters. Starts with: {api_key[:7]}...")
else:
    print("Key NOT found - check the .env file.")
