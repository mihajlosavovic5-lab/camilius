import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

EMAIL_ADRESA = os.getenv("EMAIL_ADDRESS")
EMAIL_LOZINKA = os.getenv("EMAIL_APP_PASSWORD").replace(" ", "")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587


def posalji_email(primalac, naslov, telo):
    poruka = MIMEText(telo, "plain", "utf-8")
    poruka["Subject"] = naslov
    poruka["From"] = EMAIL_ADRESA
    poruka["To"] = primalac

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_ADRESA, EMAIL_LOZINKA)
        server.send_message(poruka)


if __name__ == "__main__":
    posalji_email(
        primalac=EMAIL_ADRESA,
        naslov="Test poruka iz ai-auto-reply projekta",
        telo="Ako vidis ovo, slanje email-a radi ispravno.",
    )
    print("Poruka poslata.")
