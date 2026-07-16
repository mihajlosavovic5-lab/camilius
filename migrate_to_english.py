"""One-off migration: renames all tables/columns from Serbian to English.

Idempotent - safe to run more than once (checks information_schema before
renaming each table/column, skips anything already renamed). Run this only
once the rest of the codebase has been rewritten to use the new English
names, since the live app will error against the old names the moment this
runs and against the new names until it does.
"""
import os
import sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def konektuj():
    return psycopg2.connect(os.environ["DATABASE_URL"])


TABLE_RENAMES = [
    ("biznisi", "businesses"),
    ("razgovori", "conversations"),
    ("obradjene_poruke", "processed_messages"),
]

COLUMN_RENAMES = {
    "businesses": [
        ("naziv", "name"),
        ("korisnicko_ime", "username"),
        ("lozinka", "password"),
        ("email_adresa", "email"),
        ("radno_vreme", "working_hours"),
        ("cenovnik", "price_list"),
        ("cesta_pitanja", "faq"),
        ("reset_istice", "reset_expires"),
        ("pretplata_status", "subscription_status"),
    ],
    "conversations": [
        ("biznis_id", "business_id"),
        ("posiljalac", "sender"),
        ("naslov", "subject"),
        ("pitanje", "question"),
        ("predlog_odgovora", "suggested_reply"),
        ("konacan_odgovor", "final_reply"),
        ("vreme", "created_at"),
    ],
    "processed_messages": [
        ("biznis_id", "business_id"),
        ("vreme", "created_at"),
    ],
}


def table_exists(kursor, ime):
    kursor.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (ime,))
    return kursor.fetchone() is not None


def column_exists(kursor, tabela, kolona):
    kursor.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (tabela, kolona),
    )
    return kursor.fetchone() is not None


def migriraj():
    konekcija = konektuj()
    kursor = konekcija.cursor()

    for staro, novo in TABLE_RENAMES:
        if table_exists(kursor, staro):
            print(f"Preimenovanje tabele: {staro} -> {novo}")
            kursor.execute(f"ALTER TABLE {staro} RENAME TO {novo}")
        elif not table_exists(kursor, novo):
            raise RuntimeError(f"Ni {staro} ni {novo} ne postoje - nesto nije u redu.")
        else:
            print(f"Tabela vec preimenovana: {novo}")

    for tabela, kolone in COLUMN_RENAMES.items():
        for staro, novo in kolone:
            if column_exists(kursor, tabela, staro):
                print(f"  {tabela}.{staro} -> {novo}")
                kursor.execute(f"ALTER TABLE {tabela} RENAME COLUMN {staro} TO {novo}")
            elif not column_exists(kursor, tabela, novo):
                raise RuntimeError(f"Ni {tabela}.{staro} ni {tabela}.{novo} ne postoje.")

    # Vrednosti statusa razgovora su takodje bile na srpskom
    kursor.execute("UPDATE conversations SET status = 'pending' WHERE status = 'na_cekanju'")
    kursor.execute("UPDATE conversations SET status = 'sent' WHERE status = 'poslato'")

    konekcija.commit()
    konekcija.close()
    print("Migracija zavrsena.")


if __name__ == "__main__":
    migriraj()
