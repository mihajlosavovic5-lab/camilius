import sys
sys.stdout.reconfigure(encoding="utf-8")

import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()

with open("business_info.json", "r", encoding="utf-8") as f:
    biznis = json.load(f)


def napravi_sistemski_prompt(biznis):
    return f"""Ti si asistent koji u ime biznisa "{biznis['naziv_biznisa']}" odgovara na poruke kupaca.

Podaci o biznisu (koristi ih da odgovoriš tačno i korisno):
{json.dumps(biznis, ensure_ascii=False, indent=2)}

Pravila:
- Odgovaraj kratko, ljubazno i na srpskom jeziku.
- Koristi samo podatke koji su ti dati iznad. Ako ne znaš odgovor na osnovu njih, reci da ćeš proslediti pitanje vlasniku.
- Ne izmišljaj cene, radno vreme ili druge podatke koji nisu u listi.
"""


def generisi_odgovor(pitanje_kupca, biznis):
    sistemski_prompt = napravi_sistemski_prompt(biznis)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": sistemski_prompt},
            {"role": "user", "content": pitanje_kupca},
        ],
    )

    return response.choices[0].message.content


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Pitanje prosledjeno kao argument (npr. python generate_reply.py "Da li radite subotom?")
        # koristi se za automatsko testiranje, bez kucanja u terminalu.
        pitanje = " ".join(sys.argv[1:])
        print(f"Kupac: {pitanje}")
        odgovor = generisi_odgovor(pitanje, biznis)
        print(f"AI: {odgovor}")
    else:
        print("Ukucaj pitanje kupca (ili 'kraj' za izlaz):\n")
        while True:
            pitanje = input("Kupac: ")
            if pitanje.strip().lower() == "kraj":
                break
            odgovor = generisi_odgovor(pitanje, biznis)
            print(f"AI: {odgovor}\n")
