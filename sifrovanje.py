import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

_kljuc = os.getenv("ENCRYPTION_KEY")
_fernet = Fernet(_kljuc)


def sifruj(tekst):
    return _fernet.encrypt(tekst.encode()).decode()


def desifruj(sifrovan_tekst):
    return _fernet.decrypt(sifrovan_tekst.encode()).decode()
