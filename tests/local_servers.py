"""tests/local_servers.py — REAL local HTTP(S) servers for the client
migration / update-required / auth tests (V0.8.6 §7: "Wenn möglich echte
lokale HTTP-Server statt reiner Mock-Objekte verwenden").

Two kinds:

  - FakeGarageServer: a tiny FastAPI app whose GET /health body the test
    controls, and which RECORDS every request it receives (path, query,
    whether an Authorization header or any body was sent). That log is
    how the tests prove "no credentials were sent to the wrong URL".
  - RealAppServer: this project's actual server.main.app served over real
    HTTP(S), so the real Uploader can talk to the real upload endpoint.

Both run under uvicorn in a background thread on a free localhost port,
optionally with TLS using a throwaway self-signed certificate. For TLS
the tests point `requests` at that certificate via REQUESTS_CA_BUNDLE
(see `trust_certificate`) — the client's certificate verification stays
fully ON, exactly as in production; the test just adds its own CA to what
it trusts. Nothing here ever disables verification.
"""

from __future__ import annotations

import datetime
import ipaddress
import socket
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Union

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_self_signed_cert(directory: Path) -> tuple:
    """Self-signed cert valid for 127.0.0.1/localhost. Returns
    (cert_path, key_path)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "test-cert.pem"
    key_path = directory / "test-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class _ServerThread:
    def __init__(self, app, tls: Optional[tuple]):
        self.port = free_port()
        self.scheme = "https" if tls else "http"
        kwargs = {}
        if tls:
            kwargs = {"ssl_certfile": str(tls[0]), "ssl_keyfile": str(tls[1])}
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error", **kwargs)
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"{self.scheme}://127.0.0.1:{self.port}"

    def start(self) -> "_ServerThread":
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("local test server failed to start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


class FakeGarageServer(_ServerThread):
    """`health` may be a dict (returned as JSON with 200), a callable
    returning one, or an int status code (returned with that status and a
    non-JSON body) to simulate a broken server."""

    def __init__(self, health: Union[dict, int, Callable[[Request], dict]], tls: Optional[tuple] = None):
        self.requests_seen: List[dict] = []
        app = FastAPI()
        outer = self

        @app.middleware("http")
        async def record(request: Request, call_next):
            body = await request.body()
            outer.requests_seen.append({
                "method": request.method,
                "path": request.url.path,
                "query": dict(request.query_params),
                "has_authorization": "authorization" in request.headers,
                "body_len": len(body),
            })
            return await call_next(request)

        @app.get("/health")
        def _health(request: Request):
            if isinstance(health, int):
                return PlainTextResponse("not json at all", status_code=health)
            return JSONResponse(health(request) if callable(health) else health)

        super().__init__(app, tls)


class RealAppServer(_ServerThread):
    """This project's real FastAPI app over real HTTP(S)."""

    def __init__(self, tls: Optional[tuple] = None):
        from server.main import app

        super().__init__(app, tls)
