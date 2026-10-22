"""
scripts/rotate_secret_key.py — re-encrypt every stored client secret with the current key (V0.9.8).

The client secrets (the desktop clients' HMAC keys) are stored encrypted with
LMU_GARAGE_SECRET_KEY (server/crypto.py). To change that key without logging anybody out:

  1. generate a new key:
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  2. in .env:  LMU_GARAGE_SECRET_KEY=<new key>,<old key>      (new first) — restart the stack
  3. python -m scripts.rotate_secret_key            dry run: how many secrets still need the old key
     python -m scripts.rotate_secret_key --apply    re-encrypt them with the new key
  4. in .env:  LMU_GARAGE_SECRET_KEY=<new key>      — restart; the old key is gone

Run it inside the server container with the server's database URL (docs/RUNBOOKS.md has the
one-line command). Idempotent: running --apply twice changes nothing the second time. A secret
no configured key can read is reported (that driver has to rotate his secret in the client) and
left as it is.
"""

from __future__ import annotations

import argparse
import sys

from server.crypto import current_key_decrypts, decrypt_secret, rotate_secret
from server.database import SessionLocal
from server.models import Driver


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Re-encrypt stored client secrets with the current key.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    pending = done = unreadable = 0
    with SessionLocal() as db:
        for driver in db.query(Driver).order_by(Driver.id).all():
            if current_key_decrypts(driver.client_secret):
                continue
            try:
                decrypt_secret(driver.client_secret)
            except ValueError:
                unreadable += 1
                print(f"driver {driver.id}: no configured key can read the secret — he has to rotate it in the client")
                continue
            pending += 1
            if args.apply:
                driver.client_secret = rotate_secret(driver.client_secret)
                done += 1
        if args.apply:
            db.commit()
    print(f"{pending} secret(s) still encrypted with an old key" + (f", {done} re-encrypted" if args.apply else " (dry run — add --apply)")
          + (f"; {unreadable} unreadable" if unreadable else ""))
    return 1 if unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
