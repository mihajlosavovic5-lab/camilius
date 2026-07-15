import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import resend
from dotenv import load_dotenv

load_dotenv()

resend.api_key = os.getenv("RESEND_API_KEY")

POSILJALAC_ADRESA = "mail@ai-auto-reply-mail.com"


def posalji_email(primalac, naslov, telo, biznis_naziv, reply_to):
    resend.Emails.send({
        "from": f"{biznis_naziv} <{POSILJALAC_ADRESA}>",
        "to": [primalac],
        "reply_to": reply_to,
        "subject": naslov,
        "text": telo,
    })


if __name__ == "__main__":
    posalji_email(
        primalac=os.getenv("EMAIL_ADDRESS"),
        naslov="Test poruka iz ai-auto-reply projekta",
        telo="Ako vidis ovo, slanje email-a radi ispravno.",
        biznis_naziv="Test biznis",
        reply_to=os.getenv("EMAIL_ADDRESS"),
    )
    print("Poruka poslata.")
