import os
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import psycopg2
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import check_password_hash

from baza import (
    razgovori_za_biznis, jedan_razgovor, oznaci_poslato, biznis_po_korisnickom_imenu,
    biznis_po_id, dodaj_biznis, napravi_bazu, obrisi_biznis, broj_odgovora_danas,
    azuriraj_biznis, biznis_po_emailu, sacuvaj_reset_token, biznis_po_reset_tokenu, postavi_novu_lozinku,
    povezi_pretplatu, azuriraj_status_pretplate, status_pretplate, pretplata_aktivna,
)
from send_email import posalji_email
from read_email import proveri_kredencijale

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY nije podesen u environment varijablama.")

napravi_bazu()

limiter = Limiter(get_remote_address, app=app, default_limits=[])


def prijava_potrebna(funkcija):
    @wraps(funkcija)
    def obavijena_funkcija(*args, **kwargs):
        if not session.get("biznis_id"):
            return redirect(url_for("login"))
        # Sesija moze da pokazuje na biznis koji vise ne postoji (npr. nalog obrisan
        # u drugom tabu) - u tom slucaju cistimo sesiju i saljemo na login
        if biznis_po_id(session["biznis_id"]) is None:
            session.clear()
            return redirect(url_for("login"))
        return funkcija(*args, **kwargs)
    return obavijena_funkcija


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes", methods=["POST"])
def login():
    if request.method == "POST":
        korisnicko_ime = request.form["korisnicko_ime"]
        lozinka = request.form["lozinka"]

        biznis = biznis_po_korisnickom_imenu(korisnicko_ime)

        if biznis and check_password_hash(biznis[3], lozinka):
            session["biznis_id"] = biznis[0]
            session["biznis_naziv"] = biznis[1]
            return redirect(url_for("pocetna"))

        return render_template("login.html", greska="Pogresno korisnicko ime ili lozinka.", poruka=None)

    return render_template("login.html", greska=None, poruka=request.args.get("poruka"))


@app.route("/registracija", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def registracija():
    if request.method == "POST":
        if not request.form.get("prihvatam_uslove"):
            return render_template("registracija.html", greska="Morate prihvatiti Uslove koriscenja i Politiku privatnosti.", podaci=request.form)

        # Proveravamo da uneti Gmail + app password zaista rade pre nego sto napravimo nalog.
        # Ovo dokazuje da korisnik kontrolise nalog i da ce sistem moci da cita poruke.
        if not proveri_kredencijale(request.form["email_adresa"], request.form["email_app_password"]):
            return render_template(
                "registracija.html",
                greska="Nismo mogli da se povezemo na Gmail sa tom adresom i app password-om. Proveri da je app password ispravan (16 karaktera iz Google naloga) i da je IMAP ukljucen u Gmail podesavanjima.",
                podaci=request.form,
            )

        try:
            novi_id = dodaj_biznis(
                naziv=request.form["naziv"],
                korisnicko_ime=request.form["korisnicko_ime"],
                lozinka=request.form["lozinka"],
                email_adresa=request.form["email_adresa"],
                email_app_password=request.form["email_app_password"],
                radno_vreme=request.form["radno_vreme"],
                cenovnik=request.form["cenovnik"],
                cesta_pitanja=request.form["cesta_pitanja"],
            )
        except psycopg2.errors.UniqueViolation:
            return render_template("registracija.html", greska="To korisnicko ime ili email adresa su vec zauzeti.", podaci=request.form)

        session["biznis_id"] = novi_id
        session["biznis_naziv"] = request.form["naziv"]
        return redirect(url_for("pocetna"))

    return render_template("registracija.html", greska=None, podaci={})


@app.route("/zaboravljena-lozinka", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def zaboravljena_lozinka():
    if request.method == "POST":
        biznis = biznis_po_emailu(request.form["email_adresa"])

        if biznis:
            token = secrets.token_urlsafe(32)
            istice = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            sacuvaj_reset_token(biznis[0], token, istice)

            link = url_for("reset_lozinke", token=token, _external=True)
            posalji_email(
                primalac=biznis[4],
                naslov="Resetovanje lozinke - ai-auto-reply",
                telo=(
                    f"Zdravo,\n\n"
                    f"Neko (nadamo se ti) je zatrazio resetovanje lozinke za nalog '{biznis[1]}'.\n\n"
                    f"Klikni na link da postavis novu lozinku (vazi 1 sat):\n{link}\n\n"
                    f"Ako nisi ti trazio/la ovo, slobodno ignorisi ovaj mejl."
                ),
                biznis_naziv="ai-auto-reply",
                reply_to=os.getenv("ALERT_EMAIL") or biznis[4],
            )

        # Ista poruka bez obzira da li je email pronadjen - ne otkrivamo da li nalog postoji
        return render_template(
            "zaboravljena_lozinka.html",
            poruka="Ako je taj email registrovan, poslali smo link za resetovanje lozinke.",
        )

    return render_template("zaboravljena_lozinka.html", poruka=None)


@app.route("/reset-lozinke/<token>", methods=["GET", "POST"])
def reset_lozinke(token):
    biznis = biznis_po_reset_tokenu(token)

    if biznis is None:
        return render_template("reset_lozinke.html", nevazeci=True)

    if request.method == "POST":
        nova_lozinka = request.form["lozinka"]
        postavi_novu_lozinku(biznis[0], nova_lozinka)
        return redirect(url_for("login", poruka="Lozinka je promenjena. Prijavi se novom lozinkom."))

    return render_template("reset_lozinke.html", nevazeci=False)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/podesavanja", methods=["GET", "POST"])
@prijava_potrebna
def podesavanja():
    biznis = biznis_po_id(session["biznis_id"])
    # indeksi: 0 id, 1 naziv, 4 email_adresa, 6 radno_vreme, 7 cenovnik, 8 cesta_pitanja
    email_adresa = biznis[4]

    if request.method == "POST":
        naziv = request.form["naziv"]
        radno_vreme = request.form["radno_vreme"]
        cenovnik = request.form["cenovnik"]
        cesta_pitanja = request.form["cesta_pitanja"]
        # Prazno polje = zadrzi postojeci app password; popunjeno = promeni ga
        novi_app_password = request.form.get("email_app_password", "").strip() or None

        # Ako menja app password, prvo proverimo da novi zaista radi (kao pri registraciji)
        if novi_app_password and not proveri_kredencijale(email_adresa, novi_app_password):
            return render_template(
                "podesavanja.html",
                greska="Novi app password ne radi - nismo mogli da se povezemo na Gmail. Stari podaci su zadrzani.",
                biznis={"naziv": naziv, "email_adresa": email_adresa, "radno_vreme": radno_vreme, "cenovnik": cenovnik, "cesta_pitanja": cesta_pitanja},
                poruka=None,
            )

        azuriraj_biznis(session["biznis_id"], naziv, radno_vreme, cenovnik, cesta_pitanja, email_app_password=novi_app_password)
        session["biznis_naziv"] = naziv

        return render_template(
            "podesavanja.html",
            greska=None,
            poruka="Podaci su sacuvani.",
            biznis={"naziv": naziv, "email_adresa": email_adresa, "radno_vreme": radno_vreme, "cenovnik": cenovnik, "cesta_pitanja": cesta_pitanja},
        )

    return render_template(
        "podesavanja.html",
        greska=None,
        poruka=None,
        biznis={"naziv": biznis[1], "email_adresa": email_adresa, "radno_vreme": biznis[6], "cenovnik": biznis[7], "cesta_pitanja": biznis[8]},
    )


@app.route("/obrisi-nalog", methods=["POST"])
@prijava_potrebna
def obrisi_nalog():
    obrisi_biznis(session["biznis_id"])
    session.clear()
    return redirect(url_for("login"))


@app.route("/privatnost")
def privatnost():
    return render_template("privatnost.html")


@app.route("/uslovi")
def uslovi():
    return render_template("uslovi.html")


DNEVNI_LIMIT_ODGOVORA = 50  # isti limit kao u obradi_email.py


@app.route("/")
def pocetna():
    # Neulogovani posetioci vide javnu landing stranicu (marketing + poziv na
    # registraciju) umesto redirect-a na login. Ulogovani vide dashboard.
    if not session.get("biznis_id") or biznis_po_id(session["biznis_id"]) is None:
        session.clear()
        return render_template("landing.html")

    razgovori = razgovori_za_biznis(session["biznis_id"])

    # Filter po statusu preko ?filter=na_cekanju ili ?filter=poslato (podrazumevano: svi)
    filter_status = request.args.get("filter")
    if filter_status in ("na_cekanju", "poslato"):
        prikazani = [r for r in razgovori if r[6] == filter_status]
    else:
        filter_status = "svi"
        prikazani = razgovori

    broj_na_cekanju = sum(1 for r in razgovori if r[6] == "na_cekanju")
    danas = broj_odgovora_danas(session["biznis_id"])

    return render_template(
        "index.html",
        razgovori=prikazani,
        biznis_naziv=session["biznis_naziv"],
        filter_status=filter_status,
        broj_svi=len(razgovori),
        broj_na_cekanju=broj_na_cekanju,
        danas=danas,
        dnevni_limit=DNEVNI_LIMIT_ODGOVORA,
    )


@app.route("/posalji/<int:id_razgovora>", methods=["POST"])
@prijava_potrebna
def posalji(id_razgovora):
    konacan_odgovor = request.form["odgovor"]

    _, biznis_id, posiljalac, naslov, pitanje, predlog = jedan_razgovor(id_razgovora)

    # Bezbednosna provera: ovaj razgovor mora pripadati ulogovanom biznisu,
    # inace bi neko mogao da pogodi tudje ID-jeve u URL-u i salje tudje odgovore
    if biznis_id != session["biznis_id"]:
        abort(403)

    biznis = biznis_po_id(biznis_id)

    posalji_email(
        primalac=posiljalac,
        naslov=f"Re: {naslov}",
        telo=konacan_odgovor,
        biznis_naziv=biznis[1],
        reply_to=biznis[4],
    )

    oznaci_poslato(id_razgovora, konacan_odgovor)

    return redirect(url_for("pocetna"))


# --- Pretplata (Paddle) ---

@app.route("/pretplata")
@prijava_potrebna
def pretplata():
    biznis = biznis_po_id(session["biznis_id"])
    status = status_pretplate(session["biznis_id"])
    return render_template(
        "pretplata.html",
        client_token=os.getenv("PADDLE_CLIENT_TOKEN", ""),
        price_id=os.getenv("PADDLE_PRICE_ID", ""),
        paddle_env=os.getenv("PADDLE_ENV", "sandbox"),
        biznis_id=session["biznis_id"],
        email=biznis[4],
        status=status,
        aktivna=pretplata_aktivna(status),
    )


def proveri_paddle_potpis(header, sirovo_telo, tajni_kljuc):
    """Proverava Paddle-Signature header (HMAC-SHA256).

    Format headera: 'ts=<timestamp>;h1=<hash>'. Potpisani payload je
    '<ts>:<sirovo_telo>'. Ovim dokazujemo da zahtev stvarno dolazi od Paddle-a,
    a ne od nekog ko pogodi URL webhook-a i pokusa da lazira pretplatu.
    """
    try:
        delovi = dict(par.split("=", 1) for par in header.split(";"))
        ts = delovi["ts"]
        h1 = delovi["h1"]
    except (ValueError, KeyError):
        return False

    potpisano = ts.encode() + b":" + sirovo_telo
    ocekivano = hmac.new(tajni_kljuc.encode(), potpisano, hashlib.sha256).hexdigest()
    return hmac.compare_digest(ocekivano, h1)


@app.route("/paddle-webhook", methods=["POST"])
def paddle_webhook():
    tajni_kljuc = os.getenv("PADDLE_WEBHOOK_SECRET")
    if not tajni_kljuc:
        # Nije jos podeseno - odbijamo da ne bismo tiho primali nepotpisane zahteve
        abort(503)

    sirovo_telo = request.get_data()
    if not proveri_paddle_potpis(request.headers.get("Paddle-Signature", ""), sirovo_telo, tajni_kljuc):
        abort(400)

    dogadjaj = request.get_json(silent=True) or {}
    tip = dogadjaj.get("event_type", "")
    podaci = dogadjaj.get("data", {})

    # Zanimaju nas samo dogadjaji o pretplati (subscription.created/activated/updated/canceled...)
    if tip.startswith("subscription."):
        sub_id = podaci.get("id")
        status = podaci.get("status")
        customer_id = podaci.get("customer_id")
        # Nas biznis_id smo poslali kroz checkout kao custom_data - tako povezujemo
        # Paddle pretplatu sa nasim nalogom pri prvom dogadjaju
        biznis_id = (podaci.get("custom_data") or {}).get("biznis_id")

        if biznis_id:
            povezi_pretplatu(int(biznis_id), customer_id, sub_id, status)
        elif sub_id:
            azuriraj_status_pretplate(sub_id, status)

    # Paddle ocekuje 2xx da bi smatrao webhook isporucenim; inace ponavlja slanje
    return "", 200


if __name__ == "__main__":
    debug_mod = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mod)
