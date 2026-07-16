import sys
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI()

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "user", "content": "Tell me one sentence about why Python is a good language for beginners."}
    ]
)

print(response.choices[0].message.content)
