import sqlite3

IME_BAZE = "razgovori.db"


def napravi_bazu():
    konekcija = sqlite3.connect(IME_BAZE)
    kursor = konekcija.cursor()

    kursor.execute("""
        CREATE TABLE IF NOT EXISTS razgovori (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posiljalac TEXT NOT NULL,
            naslov TEXT,
            pitanje TEXT NOT NULL,
            predlog_odgovora TEXT NOT NULL,
            konacan_odgovor TEXT,
            status TEXT NOT NULL DEFAULT 'na_cekanju',
            vreme TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    konekcija.commit()
    konekcija.close()


def sacuvaj_predlog(posiljalac, naslov, pitanje, predlog_odgovora):
    konekcija = sqlite3.connect(IME_BAZE)
    kursor = konekcija.cursor()

    kursor.execute(
        "INSERT INTO razgovori (posiljalac, naslov, pitanje, predlog_odgovora) VALUES (?, ?, ?, ?)",
        (posiljalac, naslov, pitanje, predlog_odgovora),
    )

    konekcija.commit()
    novi_id = kursor.lastrowid
    konekcija.close()
    return novi_id


def oznaci_poslato(id_razgovora, konacan_odgovor):
    konekcija = sqlite3.connect(IME_BAZE)
    kursor = konekcija.cursor()

    kursor.execute(
        "UPDATE razgovori SET status = 'poslato', konacan_odgovor = ? WHERE id = ?",
        (konacan_odgovor, id_razgovora),
    )

    konekcija.commit()
    konekcija.close()


def jedan_razgovor(id_razgovora):
    konekcija = sqlite3.connect(IME_BAZE)
    kursor = konekcija.cursor()

    kursor.execute("SELECT id, posiljalac, naslov, pitanje, predlog_odgovora FROM razgovori WHERE id = ?", (id_razgovora,))
    red = kursor.fetchone()

    konekcija.close()
    return red


def svi_razgovori():
    konekcija = sqlite3.connect(IME_BAZE)
    kursor = konekcija.cursor()

    kursor.execute("""
        SELECT id, posiljalac, naslov, pitanje, predlog_odgovora, konacan_odgovor, status, vreme
        FROM razgovori
        ORDER BY vreme DESC
    """)
    rezultat = kursor.fetchall()

    konekcija.close()
    return rezultat


if __name__ == "__main__":
    napravi_bazu()
    print(f"Baza '{IME_BAZE}' je spremna.")
