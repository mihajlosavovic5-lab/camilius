import sqlite3
import json

IME_BAZE = "razgovori.db"


def konektuj():
    konekcija = sqlite3.connect(IME_BAZE)
    konekcija.execute("PRAGMA foreign_keys = ON")
    return konekcija


def napravi_bazu():
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute("""
        CREATE TABLE IF NOT EXISTS biznisi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            naziv TEXT NOT NULL,
            korisnicko_ime TEXT NOT NULL UNIQUE,
            lozinka TEXT NOT NULL,
            email_adresa TEXT NOT NULL UNIQUE,
            email_app_password TEXT NOT NULL,
            radno_vreme TEXT NOT NULL,
            cenovnik TEXT NOT NULL,
            cesta_pitanja TEXT NOT NULL
        )
    """)

    kursor.execute("""
        CREATE TABLE IF NOT EXISTS razgovori (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            biznis_id INTEGER NOT NULL,
            posiljalac TEXT NOT NULL,
            naslov TEXT,
            pitanje TEXT NOT NULL,
            predlog_odgovora TEXT NOT NULL,
            konacan_odgovor TEXT,
            status TEXT NOT NULL DEFAULT 'na_cekanju',
            vreme TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (biznis_id) REFERENCES biznisi(id)
        )
    """)

    konekcija.commit()
    konekcija.close()


# --- Biznisi ---

def dodaj_biznis(naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute(
        """INSERT INTO biznisi
           (naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            naziv,
            korisnicko_ime,
            lozinka,
            email_adresa,
            email_app_password,
            json.dumps(radno_vreme, ensure_ascii=False),
            json.dumps(cenovnik, ensure_ascii=False),
            json.dumps(cesta_pitanja, ensure_ascii=False),
        ),
    )

    konekcija.commit()
    novi_id = kursor.lastrowid
    konekcija.close()
    return novi_id


def svi_biznisi():
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute("""
        SELECT id, naziv, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja
        FROM biznisi
    """)
    rezultat = kursor.fetchall()
    konekcija.close()
    return rezultat


def biznis_po_id(biznis_id):
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "SELECT id, naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja FROM biznisi WHERE id = ?",
        (biznis_id,),
    )
    red = kursor.fetchone()
    konekcija.close()
    return red


def biznis_po_korisnickom_imenu(korisnicko_ime):
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "SELECT id, naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja FROM biznisi WHERE korisnicko_ime = ?",
        (korisnicko_ime,),
    )
    red = kursor.fetchone()
    konekcija.close()
    return red


# --- Razgovori ---

def sacuvaj_predlog(biznis_id, posiljalac, naslov, pitanje, predlog_odgovora):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute(
        "INSERT INTO razgovori (biznis_id, posiljalac, naslov, pitanje, predlog_odgovora) VALUES (?, ?, ?, ?, ?)",
        (biznis_id, posiljalac, naslov, pitanje, predlog_odgovora),
    )

    konekcija.commit()
    novi_id = kursor.lastrowid
    konekcija.close()
    return novi_id


def oznaci_poslato(id_razgovora, konacan_odgovor):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute(
        "UPDATE razgovori SET status = 'poslato', konacan_odgovor = ? WHERE id = ?",
        (konacan_odgovor, id_razgovora),
    )

    konekcija.commit()
    konekcija.close()


def jedan_razgovor(id_razgovora):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute(
        "SELECT id, biznis_id, posiljalac, naslov, pitanje, predlog_odgovora FROM razgovori WHERE id = ?",
        (id_razgovora,),
    )
    red = kursor.fetchone()

    konekcija.close()
    return red


def razgovori_za_biznis(biznis_id):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute("""
        SELECT id, posiljalac, naslov, pitanje, predlog_odgovora, konacan_odgovor, status, vreme
        FROM razgovori
        WHERE biznis_id = ?
        ORDER BY vreme DESC
    """, (biznis_id,))
    rezultat = kursor.fetchall()

    konekcija.close()
    return rezultat


if __name__ == "__main__":
    napravi_bazu()
    print(f"Baza '{IME_BAZE}' je spremna.")
