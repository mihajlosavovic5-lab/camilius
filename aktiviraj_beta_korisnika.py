"""Rucna aktivacija beta korisnika bez Paddle naplate.

Beta je besplatna - umesto da korisnik prolazi kroz checkout, ovde mu
direktno postavljamo pretplata_status na 'active' po korisnickom imenu.

Upotreba: python aktiviraj_beta_korisnika.py <korisnicko_ime> [status]
(status podrazumevano 'active'; moze i 'none'/'canceled' da se ukine pristup)
"""
import sys
from dotenv import load_dotenv

load_dotenv()

from baza import konektuj, biznis_po_korisnickom_imenu


def azuriraj_status(korisnicko_ime, status):
    biznis = biznis_po_korisnickom_imenu(korisnicko_ime)
    if biznis is None:
        print(f"Nema biznisa sa korisnickim imenom '{korisnicko_ime}'.")
        return

    konekcija = konektuj()
    kursor = konekcija.cursor()
    kursor.execute(
        "UPDATE biznisi SET pretplata_status = %s WHERE korisnicko_ime = %s",
        (status, korisnicko_ime),
    )
    konekcija.commit()
    konekcija.close()
    print(f"'{biznis[1]}' ({korisnicko_ime}) -> pretplata_status = '{status}'")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Upotreba: python aktiviraj_beta_korisnika.py <korisnicko_ime> [status]")
        sys.exit(1)

    korisnicko_ime_arg = sys.argv[1]
    status_arg = sys.argv[2] if len(sys.argv) == 3 else "active"
    azuriraj_status(korisnicko_ime_arg, status_arg)
