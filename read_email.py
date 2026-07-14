import sys
sys.stdout.reconfigure(encoding="utf-8")

import imaplib
import email
import os
from dotenv import load_dotenv

load_dotenv()

EMAIL_ADRESA = os.getenv("EMAIL_ADDRESS")
EMAIL_LOZINKA = os.getenv("EMAIL_APP_PASSWORD").replace(" ", "")

IMAP_SERVER = "imap.gmail.com"


def uzmi_nove_poruke():
    # Povezujemo se na Gmail-ov IMAP server preko sigurne (SSL) konekcije
    konekcija = imaplib.IMAP4_SSL(IMAP_SERVER)
    konekcija.login(EMAIL_ADRESA, EMAIL_LOZINKA)

    # Biramo koji "folder" gledamo - INBOX je glavno prijemno sanduce
    konekcija.select("INBOX")

    # Trazimo samo NEPROCITANE poruke
    status, poruke_id = konekcija.search(None, "UNSEEN")
    lista_id = poruke_id[0].split()

    nove_poruke = []
    for msg_id in lista_id:
        status, podaci = konekcija.fetch(msg_id, "(RFC822)")
        sirova_poruka = podaci[0][1]
        poruka = email.message_from_bytes(sirova_poruka)

        nove_poruke.append({
            "posiljalac": poruka.get("From"),
            "naslov": poruka.get("Subject"),
            "tekst": izvuci_tekst(poruka),
        })

    konekcija.close()
    konekcija.logout()
    return nove_poruke


def procitaj_nove_poruke():
    # Koristi se za rucnu proveru u terminalu (Faza 6) - samo ispisuje, ne vraca nista
    nove_poruke = uzmi_nove_poruke()

    print(f"Broj neprocitanih poruka: {len(nove_poruke)}\n")
    for poruka in nove_poruke:
        print(f"Od: {poruka['posiljalac']}")
        print(f"Naslov: {poruka['naslov']}")
        print(f"Tekst: {poruka['tekst'][:200]}")
        print("-" * 40)


def izvuci_tekst(poruka):
    # Email poruke mogu imati vise delova (tekst, HTML, prilozi) - trazimo cisti tekst deo
    if poruka.is_multipart():
        for deo in poruka.walk():
            if deo.get_content_type() == "text/plain":
                return deo.get_payload(decode=True).decode(errors="ignore")
        return "(nema tekstualnog dela)"
    else:
        return poruka.get_payload(decode=True).decode(errors="ignore")


if __name__ == "__main__":
    procitaj_nove_poruke()
