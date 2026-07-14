import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, abort
from werkzeug.security import check_password_hash

from baza import razgovori_za_biznis, jedan_razgovor, oznaci_poslato, biznis_po_korisnickom_imenu, biznis_po_id
from send_email import posalji_email

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")


def prijava_potrebna(funkcija):
    @wraps(funkcija)
    def obavijena_funkcija(*args, **kwargs):
        if not session.get("biznis_id"):
            return redirect(url_for("login"))
        return funkcija(*args, **kwargs)
    return obavijena_funkcija


@app.route("/login", methods=["GET", "POST"])
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


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@prijava_potrebna
def pocetna():
    razgovori = razgovori_za_biznis(session["biznis_id"])
    return render_template("index.html", razgovori=razgovori, biznis_naziv=session["biznis_naziv"])


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
    email_adresa = biznis[4]
    email_app_password = biznis[5]

    posalji_email(
        primalac=posiljalac,
        naslov=f"Re: {naslov}",
        telo=konacan_odgovor,
        email_adresa=email_adresa,
        email_app_password=email_app_password,
    )

    oznaci_poslato(id_razgovora, konacan_odgovor)

    return redirect(url_for("pocetna"))


if __name__ == "__main__":
    app.run(debug=True)
