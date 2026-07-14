import sys
sys.stdout.reconfigure(encoding="utf-8")

import json
import time
from datetime import datetime
from email.utils import parseaddr

from read_email import uzmi_nove_poruke
from generate_reply import generisi_odgovor
from baza import napravi_bazu, sacuvaj_predlog

with open("business_info.json", "r", encoding="utf-8") as f:
    biznis = json.load(f)


def obradi_nove_poruke():
    nove_poruke = uzmi_nove_poruke()
    print(f"Broj novih poruka: {len(nove_poruke)}")

    for poruka in nove_poruke:
        ime, adresa_posiljaoca = parseaddr(poruka["posiljalac"])

        print(f"\nObradujem poruku od: {adresa_posiljaoca}")
        print(f"Pitanje: {poruka['tekst'][:100]}")

        predlog = generisi_odgovor(poruka["tekst"], biznis)
        print(f"AI predlog odgovora: {predlog[:100]}")

        sacuvaj_predlog(adresa_posiljaoca, poruka["naslov"], poruka["tekst"], predlog)
        print("Predlog sacuvan, ceka odobrenje u dashboardu.")


INTERVAL_SEKUNDE = 60


def pokreni_petlju():
    napravi_bazu()
    print(f"Pokrenuto - provera novih poruka na svakih {INTERVAL_SEKUNDE} sekundi. (Ctrl+C za prekid)")

    while True:
        sada = datetime.now().strftime("%H:%M:%S")
        try:
            print(f"\n[{sada}] Proveravam nove poruke...")
            obradi_nove_poruke()
        except Exception as greska:
            # Ne dozvoljavamo da jedna greska (npr. privremeni prekid interneta) srusi ceo program
            print(f"[{sada}] Doslo je do greske: {greska}")

        time.sleep(INTERVAL_SEKUNDE)


if __name__ == "__main__":
    pokreni_petlju()
