from flask import Flask, render_template, request, redirect, url_for

from baza import svi_razgovori, jedan_razgovor, oznaci_poslato
from send_email import posalji_email

app = Flask(__name__)


@app.route("/")
def pocetna():
    razgovori = svi_razgovori()
    return render_template("index.html", razgovori=razgovori)


@app.route("/posalji/<int:id_razgovora>", methods=["POST"])
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
