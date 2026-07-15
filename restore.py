import sys
sys.stdout.reconfigure(encoding="utf-8")

import json
import argparse

from baza import konektuj


def vrati_iz_backupa(putanja):
    """Prepisuje CELE tabele biznisi i razgovori sadrzajem iz JSON backup fajla
    (napravljenog preko backup.py). ID-jevi se vracaju tacno kakvi su bili u
    backupu da bi strani kljucevi (razgovori.biznis_id) ostali ispravni."""
    with open(putanja, "r", encoding="utf-8") as f:
        podaci = json.load(f)

    konekcija = konektuj()
    kursor = konekcija.cursor()

    # Razgovori prvo (strani kljuc ka biznisu)
    kursor.execute("DELETE FROM razgovori")
    kursor.execute("DELETE FROM biznisi")

    for red in podaci["biznisi"]:
        kursor.execute(
            """INSERT INTO biznisi
               (id, naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            tuple(red),
        )

    for red in podaci["razgovori"]:
        kursor.execute(
            """INSERT INTO razgovori
               (id, biznis_id, posiljalac, naslov, pitanje, predlog_odgovora, konacan_odgovor, status, vreme)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            tuple(red),
        )

    # SERIAL sekvence moraju da se pomere iznad vracenih ID-jeva, inace bi sledeca
    # nova registracija/razgovor mogla da pokusa da iskoristi vec zauzet ID
    kursor.execute("SELECT setval(pg_get_serial_sequence('biznisi', 'id'), COALESCE((SELECT MAX(id) FROM biznisi), 1))")
    kursor.execute("SELECT setval(pg_get_serial_sequence('razgovori', 'id'), COALESCE((SELECT MAX(id) FROM razgovori), 1))")

    konekcija.commit()
    konekcija.close()

    return len(podaci["biznisi"]), len(podaci["razgovori"]), podaci.get("napravljen")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vraca bazu iz JSON backup fajla. BRISE sve trenutne podatke!")
    parser.add_argument("putanja", help="Putanja do backup-YYYY-MM-DD.json fajla")
    parser.add_argument("--da", action="store_true", help="Potvrdi da stvarno zelis da prepises trenutnu bazu")
    args = parser.parse_args()

    if not args.da:
        print("Ovo BRISE sve trenutne podatke u bazi i vraca ih iz backupa.")
        print("Ako si siguran/na, pokreni ponovo sa --da")
        sys.exit(1)

    broj_biznisa, broj_razgovora, napravljen = vrati_iz_backupa(args.putanja)
    print(f"Vraceno iz backupa (napravljen {napravljen}): {broj_biznisa} biznisa, {broj_razgovora} razgovora.")
