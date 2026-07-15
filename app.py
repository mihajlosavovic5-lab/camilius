import os
import psycopg2
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import check_password_hash

from baza import razgovori_za_biznis, jedan_razgovor, oznaci_poslato, biznis_po_korisnickom_imenu, biznis_po_id, dodaj_biznis, napravi_bazu, obrisi_biznis, broj_odgovora_danas
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

        return render_template("login.html", greska="Pogresno korisnicko ime ili lozinka.")

    return render_template("login.html", greska=None)


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


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
@prijava_potrebna
def pocetna():
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


if __name__ == "__main__":
    debug_mod = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mod)
