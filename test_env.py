from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")

if api_key:
    print(f"Kljuc je ucitan. Duzina: {len(api_key)} karaktera. Pocinje sa: {api_key[:7]}...")
else:
    print("Kljuc NIJE pronadjen - proveri .env fajl.")
