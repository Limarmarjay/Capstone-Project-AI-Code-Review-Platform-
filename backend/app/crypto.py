import os

from cryptography.fernet import Fernet

# Dev-only default, same pattern as JWT_SECRET — override via env in prod.
# Generated with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY', 'DlbDvbgp7sgKfzPz0pbaeFdvp4gZhi4TX4uz8tIoh-s=')

_fernet = Fernet(ENCRYPTION_KEY.encode())


def encrypt_token(token: str) -> str:
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    return _fernet.decrypt(encrypted_token.encode()).decode()
