import sys
sys.stdout.reconfigure(encoding="utf-8")

import imaplib
import email
import os
from dotenv import load_dotenv

load_dotenv()

IMAP_SERVER = "imap.gmail.com"


def proveri_kredencijale(email_adresa, email_app_password):
    """Pokusava IMAP prijavu na Gmail. Vraca True ako kredencijali rade, inace False.

    Koristi se pri registraciji da odmah potvrdimo da uneti email + app password
    zaista funkcionisu (i time da korisnik kontrolise taj nalog), umesto da sistem
    tek kasnije tiho pukne u pozadinskoj petlji. timeout sprecava da zahtev zaglavi.
    """
    try:
        konekcija = imaplib.IMAP4_SSL(IMAP_SERVER, timeout=15)
    except Exception:
        # Mrezni problem - ne mozemo da potvrdimo
        return False

    try:
        konekcija.login(email_adresa, (email_app_password or "").replace(" ", ""))
        konekcija.logout()
        return True
    except Exception:
        # Najcesce imaplib.IMAP4.error zbog pogresnih kredencijala
        return False


def uzmi_nove_poruke(email_adresa, email_app_password, max_broj=None):
    # Povezujemo se na Gmail-ov IMAP server preko sigurne (SSL) konekcije
    konekcija = imaplib.IMAP4_SSL(IMAP_SERVER)
    konekcija.login(email_adresa, email_app_password.replace(" ", ""))

    # Biramo koji "folder" gledamo - INBOX je glavno prijemno sanduce
    konekcija.select("INBOX")

    # Trazimo samo NEPROCITANE poruke
    status, poruke_id = konekcija.search(None, "UNSEEN")
    lista_id = poruke_id[0].split()

    if max_broj is not None:
        # Uzimamo samo prvih 'max_broj' - ostale namerno ne diramo (ostaju NEPROCITANE za sledeci put)
        lista_id = lista_id[:max_broj]

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
    email_adresa = os.getenv("EMAIL_ADDRESS")
    email_app_password = (os.getenv("EMAIL_APP_PASSWORD") or "").replace(" ", "")
    nove_poruke = uzmi_nove_poruke(email_adresa, email_app_password)

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
