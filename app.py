import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session

from baza import svi_razgovori, jedan_razgovor, oznaci_poslato
from send_email import posalji_email

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")


def prijava_potrebna(funkcija):
    @wraps(funkcija)
    def obavijena_funkcija(*args, **kwargs):
        if not session.get("ulogovan"):
            return redirect(url_for("login"))
        return funkcija(*args, **kwargs)
    return obavijena_funkcija


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        korisnicko_ime = request.form["korisnicko_ime"]
        lozinka = request.form["lozinka"]

        if korisnicko_ime == ADMIN_USERNAME and lozinka == ADMIN_PASSWORD:
            session["ulogovan"] = True
            return redirect(url_for("pocetna"))

        return render_template("login.html", greska="Pogresno korisnicko ime ili lozinka.")

    return render_template("login.html", greska=None)


@app.route("/logout")
def logout():
    session.pop("ulogovan", None)
    return redirect(url_for("login"))


@app.route("/")
@prijava_potrebna
def pocetna():
    razgovori = svi_razgovori()
    return render_template("index.html", razgovori=razgovori)


@app.route("/posalji/<int:id_razgovora>", methods=["POST"])
@prijava_potrebna
def posalji(id_razgovora):
    konacan_odgovor = request.form["odgovor"]

    _, posiljalac, naslov, pitanje, predlog = jedan_razgovor(id_razgovora)

    posalji_email(
        primalac=posiljalac,
        naslov=f"Re: {naslov}",
        telo=konacan_odgovor,
    )

    oznaci_poslato(id_razgovora, konacan_odgovor)

    return redirect(url_for("pocetna"))


if __name__ == "__main__":
    app.run(debug=True)
