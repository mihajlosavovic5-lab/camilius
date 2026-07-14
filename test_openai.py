import sys
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI()

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "user", "content": "Reci mi jednu recenicu o tome zasto je Python dobar jezik za pocetnike."}
    ]
)

print(response.choices[0].message.content)
