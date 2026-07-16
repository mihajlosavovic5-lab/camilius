import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import time
from datetime import datetime
from email.utils import parseaddr

from read_email import uzmi_nove_poruke
from generate_reply import generisi_odgovor
from send_email import posalji_email
from baza import napravi_bazu, sacuvaj_predlog, svi_biznisi, broj_odgovora_danas, prethodni_razgovori_sa, pretplata_aktivna
from backup import dnevni_backup_ako_treba

DNEVNI_LIMIT_ODGOVORA = 50

# Mejl adresa na koju stizu upozorenja kad obrada nekog biznisa pukne (npr. tvoja)
ALERT_EMAIL = os.getenv("ALERT_EMAIL")

# Da ne bombardujemo alertima svakih 60s za isti problem: najvise jedan alert
# po biznisu na svakih ALERT_COOLDOWN_SEKUNDE. Pamti se u memoriji procesa.
ALERT_COOLDOWN_SEKUNDE = 6 * 60 * 60
_poslednji_alert = {}


def posalji_alert(naziv_biznisa, greska):
    if not ALERT_EMAIL:
        return

    sada = time.time()
    prosli = _poslednji_alert.get(naziv_biznisa, 0)
    if sada - prosli < ALERT_COOLDOWN_SEKUNDE:
        return

    try:
        posalji_email(
            primalac=ALERT_EMAIL,
            naslov=f"[ai-auto-reply ALERT] Greska kod biznisa: {naziv_biznisa}",
            telo=(
                f"Obrada poruka za biznis '{naziv_biznisa}' je pukla.\n\n"
                f"Greska: {greska}\n\n"
                f"Ostali biznisi se obradjuju normalno. Ako je greska IMAP prijava,\n"
                f"verovatno je biznis promenio Gmail lozinku ili opozvao app password."
            ),
            biznis_naziv="ai-auto-reply sistem",
            reply_to=ALERT_EMAIL,
        )
        _poslednji_alert[naziv_biznisa] = sada
    except Exception as e:
        # Alert ne sme da srusi petlju - samo zabelezimo u log
        print(f"[ALERT] Slanje alerta nije uspelo: {e}")


def obradi_jednog_biznisa(biznis_red):
    biznis_id, naziv, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja, pretplata_status = biznis_red

    if not pretplata_aktivna(pretplata_status):
        print(f"[{naziv}] Pretplata nije aktivna ({pretplata_status}) - preskacem.")
        return

    preostalo_danas = DNEVNI_LIMIT_ODGOVORA - broj_odgovora_danas(biznis_id)
    if preostalo_danas <= 0:
        print(f"[{naziv}] Dostignut dnevni limit ({DNEVNI_LIMIT_ODGOVORA}) - preskacem do sutra.")
        return

    biznis = {
        "naziv_biznisa": naziv,
        "radno_vreme": radno_vreme,
        "cenovnik": cenovnik,
        "cesta_pitanja": cesta_pitanja,
    }

    nove_poruke = uzmi_nove_poruke(email_adresa, email_app_password, max_broj=preostalo_danas)
    print(f"[{naziv}] Broj novih poruka: {len(nove_poruke)}")

    for poruka in nove_poruke:
        ime, adresa_posiljaoca = parseaddr(poruka["posiljalac"])

        print(f"[{naziv}] Obradujem poruku od: {adresa_posiljaoca}")

        istorija = prethodni_razgovori_sa(biznis_id, adresa_posiljaoca)
        predlog = generisi_odgovor(poruka["tekst"], biznis, istorija=istorija)

        sacuvaj_predlog(biznis_id, adresa_posiljaoca, poruka["naslov"], poruka["tekst"], predlog)
        print(f"[{naziv}] Predlog sacuvan, ceka odobrenje u dashboardu.")


def obradi_nove_poruke():
    for biznis_red in svi_biznisi():
        # Greska kod jednog biznisa (npr. promenjena Gmail lozinka) ne sme da
        # zaustavi obradu ostalih biznisa u istom ciklusu
        try:
            obradi_jednog_biznisa(biznis_red)
        except Exception as greska:
            naziv = biznis_red[1]
            print(f"[{naziv}] Greska pri obradi - preskacem ovaj biznis: {greska}")
            posalji_alert(naziv, greska)


INTERVAL_SEKUNDE = 60


def pokreni_petlju():
    napravi_bazu()
    print(f"Pokrenuto - provera novih poruka na svakih {INTERVAL_SEKUNDE} sekundi. (Ctrl+C za prekid)")

    while True:
        sada = datetime.now().strftime("%H:%M:%S")
        try:
            print(f"\n[{sada}] Proveravam nove poruke...")
            obradi_nove_poruke()
            dnevni_backup_ako_treba()
        except Exception as greska:
            # Ne dozvoljavamo da jedna greska (npr. privremeni prekid interneta) srusi ceo program
            print(f"[{sada}] Doslo je do greske: {greska}")

        time.sleep(INTERVAL_SEKUNDE)


if __name__ == "__main__":
    pokreni_petlju()
