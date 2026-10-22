"""V0.9.8 — the encryption key of the stored client secrets can be changed without locking anybody
out (several keys: the first encrypts, all decrypt; scripts/rotate_secret_key.py re-encrypts), and
production refuses to start without a real key."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from server import crypto


def test_several_keys_first_encrypts_all_decrypt(monkeypatch):
    old, new = Fernet.generate_key(), Fernet.generate_key()
    token_old = crypto.build([old]).encrypt(b"s3cret").decode()
    both = crypto.build([new, old])
    assert both.decrypt(token_old.encode()) == b"s3cret"
    rotated = both.rotate(token_old.encode())
    assert crypto.build([new]).decrypt(rotated) == b"s3cret", "after rotation the new key alone reads it"
    with pytest.raises(Exception):
        crypto.build([old]).decrypt(crypto.build([new, old]).encrypt(b"x"))   # new secrets use the new key


def test_the_key_list_comes_from_the_environment(monkeypatch):
    a, b = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("LMU_GARAGE_SECRET_KEY", f"{a}, {b}")
    assert crypto._load_keys() == [a.encode(), b.encode()]
    assert crypto._load_key() == a.encode()


def test_production_without_a_key_refuses_to_start(monkeypatch):
    from server.config import settings

    monkeypatch.delenv("LMU_GARAGE_SECRET_KEY", raising=False)
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    with pytest.raises(RuntimeError, match="refusing"):
        crypto._load_keys()


def test_a_malformed_key_fails_at_once():
    with pytest.raises(Exception):
        crypto.build([b"not-a-fernet-key"])


def test_the_rotation_script_re_encrypts_every_old_secret_and_reports_unreadable_ones(client, monkeypatch, capsys):
    from server.database import SessionLocal
    from server.models import Driver
    from scripts import rotate_secret_key
    from tests.helpers.v093 import register

    old, new = Fernet.generate_key(), Fernet.generate_key()
    monkeypatch.setattr(crypto, "_fernet", crypto.build([old]))
    monkeypatch.setenv("LMU_GARAGE_SECRET_KEY", old.decode())
    a, b = register(client, "Rot A"), register(client, "Rot B")
    with SessionLocal() as db:
        foreign = db.get(Driver, b["driver_id"])
        foreign.client_secret = Fernet(Fernet.generate_key()).encrypt(b"lost").decode()   # nobody can read this
        db.commit()
    # step 2 of the procedure: new key in front, old behind
    monkeypatch.setenv("LMU_GARAGE_SECRET_KEY", f"{new.decode()},{old.decode()}")
    monkeypatch.setattr(crypto, "_fernet", crypto.build([new, old]))
    assert rotate_secret_key.main([]) == 1                      # dry run: one pending, one unreadable
    out = capsys.readouterr().out
    assert "1 secret(s) still encrypted with an old key" in out and "1 unreadable" in out
    rotate_secret_key.main(["--apply"])
    with SessionLocal() as db:
        secret = db.get(Driver, a["driver_id"]).client_secret
    assert Fernet(new).decrypt(secret.encode()).decode() == a["client_secret"], "same secret, new key"
    rotate_secret_key.main(["--apply"])
    assert "0 secret(s)" in capsys.readouterr().out.splitlines()[-1], "idempotent"
