from cryptography.fernet import Fernet

from app.config import settings


def _cipher() -> Fernet:
    return Fernet(settings().fernet_key.encode())


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _cipher().decrypt(ciphertext.encode()).decode()
