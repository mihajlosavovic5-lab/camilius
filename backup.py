import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import json
from datetime import datetime, date

from baza import konektuj

# Folder u koji se upisuju dnevni backup fajlovi (na Railway-u: /data/backups,
# na postojecem trajnom volume-u koji prezivljava redeploy-e).
# Ako BACKUP_DIR nije podesen, backup se preskace.
BACKUP_DIR = os.getenv("BACKUP_DIR")

# Koliko poslednjih dnevnih backupa cuvamo pre brisanja najstarijih
BROJ_BACKUPA = 14


def napravi_backup():
    """Upisuje kompletan sadrzaj baze (obe tabele) u JSON fajl. Vraca putanju fajla."""
    os.makedirs(BACKUP_DIR, exist_ok=True)

    konekcija = konektuj()
    kursor = konekcija.cursor()

    podaci = {"napravljen": datetime.now().isoformat()}

    kursor.execute("SELECT id, naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja FROM biznisi ORDER BY id")
    podaci["biznisi"] = kursor.fetchall()

    kursor.execute("SELECT id, biznis_id, posiljalac, naslov, pitanje, predlog_odgovora, konacan_odgovor, status, vreme::text FROM razgovori ORDER BY id")
    podaci["razgovori"] = kursor.fetchall()

    konekcija.close()

    putanja = os.path.join(BACKUP_DIR, f"backup-{date.today().isoformat()}.json")
    with open(putanja, "w", encoding="utf-8") as f:
        json.dump(podaci, f, ensure_ascii=False)

    obrisi_stare_backupe()
    return putanja


def obrisi_stare_backupe():
    fajlovi = sorted(
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith("backup-") and f.endswith(".json")
    )
    for visak in fajlovi[:-BROJ_BACKUPA]:
        os.remove(os.path.join(BACKUP_DIR, visak))


def dnevni_backup_ako_treba():
    """Poziva se iz worker petlje - pravi backup najvise jednom dnevno.

    Danasnji fajl vec postoji -> ne radi nista. BACKUP_DIR nije podesen -> preskace.
    """
    if not BACKUP_DIR:
        return

    danasnji = os.path.join(BACKUP_DIR, f"backup-{date.today().isoformat()}.json")
    if os.path.exists(danasnji):
        return

    putanja = napravi_backup()
    print(f"[backup] Napravljen dnevni backup: {putanja}")


if __name__ == "__main__":
    if not BACKUP_DIR:
        print("BACKUP_DIR nije podesen - postavi ga pa pokreni ponovo.")
        sys.exit(1)
    print(f"Backup napravljen: {napravi_backup()}")
