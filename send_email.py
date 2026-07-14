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


def posalji_email(primalac, naslov, telo, email_adresa=None, email_app_password=None):
    # Ako posiljalac nije eksplicitno prosledjen, koristi se .env (npr. za rucno testiranje)
    email_adresa = email_adresa or EMAIL_ADRESA
    email_app_password = (email_app_password or EMAIL_LOZINKA).replace(" ", "")

    poruka = MIMEText(telo, "plain", "utf-8")
    poruka["Subject"] = naslov
    poruka["From"] = email_adresa
    poruka["To"] = primalac

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(email_adresa, email_app_password)
        server.send_message(poruka)


if __name__ == "__main__":
    posalji_email(
        primalac=EMAIL_ADRESA,
        naslov="Test poruka iz ai-auto-reply projekta",
        telo="Ako vidis ovo, slanje email-a radi ispravno.",
    )
    print("Poruka poslata.")
