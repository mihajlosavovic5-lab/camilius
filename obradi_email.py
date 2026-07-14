import sys
sys.stdout.reconfigure(encoding="utf-8")

import time
from datetime import datetime
from email.utils import parseaddr

from read_email import uzmi_nove_poruke
from generate_reply import generisi_odgovor
from baza import napravi_bazu, sacuvaj_predlog, svi_biznisi


def obradi_jednog_biznisa(biznis_red):
    biznis_id, naziv, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja = biznis_red

    biznis = {
        "naziv_biznisa": naziv,
        "radno_vreme": radno_vreme,
        "cenovnik": cenovnik,
        "cesta_pitanja": cesta_pitanja,
    }

    nove_poruke = uzmi_nove_poruke(email_adresa, email_app_password)
    print(f"[{naziv}] Broj novih poruka: {len(nove_poruke)}")

    for poruka in nove_poruke:
        ime, adresa_posiljaoca = parseaddr(poruka["posiljalac"])

        print(f"[{naziv}] Obradujem poruku od: {adresa_posiljaoca}")

        predlog = generisi_odgovor(poruka["tekst"], biznis)

        sacuvaj_predlog(biznis_id, adresa_posiljaoca, poruka["naslov"], poruka["tekst"], predlog)
        print(f"[{naziv}] Predlog sacuvan, ceka odobrenje u dashboardu.")


def obradi_nove_poruke():
    for biznis_red in svi_biznisi():
        obradi_jednog_biznisa(biznis_red)


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
