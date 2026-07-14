import sys
sys.stdout.reconfigure(encoding="utf-8")

import json

with open("business_info.json", "r", encoding="utf-8") as f:
    biznis = json.load(f)

print("Naziv:", biznis["naziv_biznisa"])
print("Radno vreme subotom:", biznis["radno_vreme"]["subota"])
print("Cena musko sisanje:", biznis["cenovnik"]["musko_sisanje"])
print("Broj cestih pitanja:", len(biznis["cesta_pitanja"]))
