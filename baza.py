import sqlite3
from werkzeug.security import generate_password_hash

from sifrovanje import sifruj, desifruj

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
            generate_password_hash(lozinka),
            email_adresa,
            sifruj(email_app_password),
            radno_vreme,
            cenovnik,
            cesta_pitanja,
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

    # email_app_password je sifrovan u bazi (indeks 3) - desifrujemo ga pre upotrebe
    return [red[:3] + (desifruj(red[3]),) + red[4:] for red in rezultat]


def biznis_po_id(biznis_id):
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "SELECT id, naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja FROM biznisi WHERE id = ?",
        (biznis_id,),
    )
    red = kursor.fetchone()
    konekcija.close()

    if red is None:
        return None
    # email_app_password je na indeksu 5 - desifrujemo ga pre upotrebe
    return red[:5] + (desifruj(red[5]),) + red[6:]


def biznis_po_korisnickom_imenu(korisnicko_ime):
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "SELECT id, naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja FROM biznisi WHERE korisnicko_ime = ?",
        (korisnicko_ime,),
    )
    red = kursor.fetchone()
    konekcija.close()

    if red is None:
        return None
    # NAPOMENA: 'lozinka' (indeks 3) ostaje hesirana - login funkcija je proverava
    # preko check_password_hash, nikad se ne desifruje. email_app_password (indeks 5) desifrujemo.
    return red[:5] + (desifruj(red[5]),) + red[6:]


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
