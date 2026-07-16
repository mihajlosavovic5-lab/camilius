import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

_key = os.getenv("ENCRYPTION_KEY")
_fernet = Fernet(_key)


def encrypt(text):
    return _fernet.encrypt(text.encode()).decode()


def decrypt(encrypted_text):
    return _fernet.decrypt(encrypted_text.encode()).decode()
