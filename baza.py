import os
import psycopg2
from werkzeug.security import generate_password_hash

from sifrovanje import sifruj, desifruj


def konektuj():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def napravi_bazu():
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute("""
        CREATE TABLE IF NOT EXISTS biznisi (
            id SERIAL PRIMARY KEY,
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
            id SERIAL PRIMARY KEY,
            biznis_id INTEGER NOT NULL REFERENCES biznisi(id),
            posiljalac TEXT NOT NULL,
            naslov TEXT,
            pitanje TEXT NOT NULL,
            predlog_odgovora TEXT NOT NULL,
            konacan_odgovor TEXT,
            status TEXT NOT NULL DEFAULT 'na_cekanju',
            vreme TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ADD COLUMN IF NOT EXISTS - resetovanje lozinke je dodato posle prvobitne seme,
    # ovo omogucava da postojeca produkciona tabela dobije te kolone bez rucne migracije
    kursor.execute("ALTER TABLE biznisi ADD COLUMN IF NOT EXISTS reset_token TEXT")
    kursor.execute("ALTER TABLE biznisi ADD COLUMN IF NOT EXISTS reset_istice TIMESTAMP")

    # Paddle pretplata - dodato posle prvobitne seme (isti razlog kao reset_token gore).
    # pretplata_status pamti Paddle-ov status pretplate: 'none' (nikad se nije pretplatio),
    # 'trialing', 'active', 'past_due', 'paused', 'canceled'. Worker obradjuje samo
    # 'trialing'/'active'. Postojeci biznisi dobijaju 'none' pa se pre ukljucivanja
    # naplate rucno "grandfather"-uju na 'active'.
    kursor.execute("ALTER TABLE biznisi ADD COLUMN IF NOT EXISTS paddle_customer_id TEXT")
    kursor.execute("ALTER TABLE biznisi ADD COLUMN IF NOT EXISTS paddle_subscription_id TEXT")
    kursor.execute("ALTER TABLE biznisi ADD COLUMN IF NOT EXISTS pretplata_status TEXT NOT NULL DEFAULT 'none'")

    konekcija.commit()
    konekcija.close()


# --- Biznisi ---

def dodaj_biznis(naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute(
        """INSERT INTO biznisi
           (naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
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

    novi_id = kursor.fetchone()[0]
    konekcija.commit()
    konekcija.close()
    return novi_id


def svi_biznisi():
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute("""
        SELECT id, naziv, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja, pretplata_status
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
        "SELECT id, naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja FROM biznisi WHERE id = %s",
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
        "SELECT id, naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja FROM biznisi WHERE korisnicko_ime = %s",
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

def broj_odgovora_danas(biznis_id):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute(
        "SELECT COUNT(*) FROM razgovori WHERE biznis_id = %s AND vreme::date = CURRENT_DATE",
        (biznis_id,),
    )
    broj = kursor.fetchone()[0]

    konekcija.close()
    return broj


def sacuvaj_predlog(biznis_id, posiljalac, naslov, pitanje, predlog_odgovora):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute(
        "INSERT INTO razgovori (biznis_id, posiljalac, naslov, pitanje, predlog_odgovora) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (biznis_id, posiljalac, naslov, pitanje, predlog_odgovora),
    )

    novi_id = kursor.fetchone()[0]
    konekcija.commit()
    konekcija.close()
    return novi_id


def oznaci_poslato(id_razgovora, konacan_odgovor):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute(
        "UPDATE razgovori SET status = 'poslato', konacan_odgovor = %s WHERE id = %s",
        (konacan_odgovor, id_razgovora),
    )

    konekcija.commit()
    konekcija.close()


def jedan_razgovor(id_razgovora):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute(
        "SELECT id, biznis_id, posiljalac, naslov, pitanje, predlog_odgovora FROM razgovori WHERE id = %s",
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
        WHERE biznis_id = %s
        ORDER BY vreme DESC
    """, (biznis_id,))
    rezultat = kursor.fetchall()

    konekcija.close()
    return rezultat


def biznis_po_emailu(email_adresa):
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "SELECT id, naziv, korisnicko_ime, lozinka, email_adresa, email_app_password, radno_vreme, cenovnik, cesta_pitanja FROM biznisi WHERE email_adresa = %s",
        (email_adresa,),
    )
    red = kursor.fetchone()
    konekcija.close()

    if red is None:
        return None
    return red[:5] + (desifruj(red[5]),) + red[6:]


def sacuvaj_reset_token(biznis_id, token, istice):
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "UPDATE biznisi SET reset_token = %s, reset_istice = %s WHERE id = %s",
        (token, istice, biznis_id),
    )
    konekcija.commit()
    konekcija.close()


def biznis_po_reset_tokenu(token):
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "SELECT id, naziv FROM biznisi WHERE reset_token = %s AND reset_istice > NOW()",
        (token,),
    )
    red = kursor.fetchone()
    konekcija.close()
    return red


def postavi_novu_lozinku(biznis_id, nova_lozinka):
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "UPDATE biznisi SET lozinka = %s, reset_token = NULL, reset_istice = NULL WHERE id = %s",
        (generate_password_hash(nova_lozinka), biznis_id),
    )
    konekcija.commit()
    konekcija.close()


def azuriraj_biznis(biznis_id, naziv, radno_vreme, cenovnik, cesta_pitanja, email_app_password=None):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    # email_app_password se azurira samo ako je prosledjen (korisnik ga je promenio);
    # inace ostaje postojeci. Kad se azurira, ponovo se sifruje.
    if email_app_password is not None:
        kursor.execute(
            """UPDATE biznisi
               SET naziv=%s, radno_vreme=%s, cenovnik=%s, cesta_pitanja=%s, email_app_password=%s
               WHERE id=%s""",
            (naziv, radno_vreme, cenovnik, cesta_pitanja, sifruj(email_app_password), biznis_id),
        )
    else:
        kursor.execute(
            """UPDATE biznisi
               SET naziv=%s, radno_vreme=%s, cenovnik=%s, cesta_pitanja=%s
               WHERE id=%s""",
            (naziv, radno_vreme, cenovnik, cesta_pitanja, biznis_id),
        )

    konekcija.commit()
    konekcija.close()


def obrisi_biznis(biznis_id):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    # Prvo razgovori (imaju strani kljuc ka biznisu), pa tek onda sam biznis
    kursor.execute("DELETE FROM razgovori WHERE biznis_id = %s", (biznis_id,))
    kursor.execute("DELETE FROM biznisi WHERE id = %s", (biznis_id,))

    konekcija.commit()
    konekcija.close()


# --- Pretplata (Paddle) ---

# Statusi u kojima biznis DOBIJA uslugu (worker mu obradjuje mejlove).
# Sve ostalo (none/past_due/paused/canceled) znaci da je obrada zaustavljena.
AKTIVNI_STATUSI = ("trialing", "active")


def pretplata_aktivna(status):
    return status in AKTIVNI_STATUSI


def povezi_pretplatu(biznis_id, paddle_customer_id, paddle_subscription_id, status):
    """Poziva se iz webhook-a kad Paddle prvi put javi pretplatu za ovaj biznis.
    Vezuje Paddle customer/subscription ID za nas biznis i upisuje status."""
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        """UPDATE biznisi
           SET paddle_customer_id = %s, paddle_subscription_id = %s, pretplata_status = %s
           WHERE id = %s""",
        (paddle_customer_id, paddle_subscription_id, status, biznis_id),
    )
    konekcija.commit()
    konekcija.close()


def azuriraj_status_pretplate(paddle_subscription_id, status):
    """Poziva se iz webhook-a na kasnije promene (trial->active, past_due, canceled...).
    Nalazi biznis po vec sacuvanom subscription ID-ju i menja mu samo status."""
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "UPDATE biznisi SET pretplata_status = %s WHERE paddle_subscription_id = %s",
        (status, paddle_subscription_id),
    )
    konekcija.commit()
    konekcija.close()


def status_pretplate(biznis_id):
    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute("SELECT pretplata_status FROM biznisi WHERE id = %s", (biznis_id,))
    red = kursor.fetchone()
    konekcija.close()
    return red[0] if red else None


def prethodni_razgovori_sa(biznis_id, posiljalac, limit=5):
    konekcija = konektuj()
    kursor = konekcija.cursor()

    kursor.execute("""
        SELECT pitanje, konacan_odgovor
        FROM razgovori
        WHERE biznis_id = %s AND posiljalac = %s AND status = 'poslato'
        ORDER BY vreme DESC
        LIMIT %s
    """, (biznis_id, posiljalac, limit))
    rezultat = kursor.fetchall()

    konekcija.close()
    return list(reversed(rezultat))


if __name__ == "__main__":
    napravi_bazu()
    print("Baza je spremna.")
