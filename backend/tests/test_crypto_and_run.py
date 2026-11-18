"""Tests for app.crypto (Fernet wrapper) and backend/run.py (entry point)."""
import importlib
import runpy
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app import crypto

RUN_PY = Path(__file__).resolve().parents[1] / 'run.py'


def test_encrypt_decrypt_round_trip():
    assert crypto.decrypt_token(crypto.encrypt_token('ghp_secret')) == 'ghp_secret'


def test_ciphertext_differs_from_plaintext():
    assert 'ghp_secret' not in crypto.encrypt_token('ghp_secret')


def test_decrypt_rejects_garbage():
    with pytest.raises(InvalidToken):
        crypto.decrypt_token('not-valid-ciphertext')


def test_decrypt_rejects_token_from_other_key():
    foreign = Fernet(Fernet.generate_key()).encrypt(b'x').decode()
    with pytest.raises(InvalidToken):
        crypto.decrypt_token(foreign)


def test_missing_encryption_key_raises(monkeypatch):
    monkeypatch.delenv('ENCRYPTION_KEY')
    try:
        with pytest.raises(RuntimeError, match='encryption key'):
            importlib.reload(crypto)
    finally:
        monkeypatch.undo()
        importlib.reload(crypto)  # restore module state for later tests


def test_run_module_exposes_app(monkeypatch):
    monkeypatch.setattr('dotenv.load_dotenv', lambda *a, **k: None)
    namespace = runpy.run_path(str(RUN_PY), run_name='not_main')
    assert namespace['app'].name == 'app'


def test_run_main_starts_dev_server(monkeypatch):
    from flask import Flask

    calls = {}
    monkeypatch.setattr(Flask, 'run', lambda self, **kw: calls.update(kw))
    monkeypatch.setattr('dotenv.load_dotenv', lambda *a, **k: None)
    runpy.run_path(str(RUN_PY), run_name='__main__')
    assert calls == {'debug': True, 'port': 5002}
