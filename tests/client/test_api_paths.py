"""The desktop client uses /api/v1 and falls back to the old paths for a server that is
not updated yet (client/api_paths.py)."""

from __future__ import annotations

import pytest

from client import api_paths


class Resp:
    def __init__(self, status, content_type="application/json"):
        self.status_code = status
        self.headers = {"content-type": content_type}


@pytest.fixture(autouse=True)
def fresh():
    api_paths.forget()
    yield
    api_paths.forget()


def test_a_new_server_is_asked_on_the_new_path():
    assert api_paths.url_for("https://garage16.de/", "/api/v1/laps/upload") == "https://garage16.de/api/v1/laps/upload"
    assert api_paths.fall_back("https://garage16.de", "/api/v1/laps/upload", Resp(200)) is False
    assert api_paths.fall_back("https://garage16.de", "/api/v1/laps/upload", Resp(401)) is False


@pytest.mark.parametrize("resp", [Resp(404), Resp(405), Resp(200, "text/html; charset=utf-8")])
def test_an_older_server_is_remembered_and_asked_on_the_old_path(resp):
    assert api_paths.fall_back("https://nas.example", "/api/v1/laps/upload", resp) is True
    assert api_paths.url_for("https://nas.example", "/api/v1/laps/upload") == "https://nas.example/telemetry/upload"
    assert api_paths.url_for("https://nas.example", "/api/v1/accounts/desktop-login") == "https://nas.example/accounts/desktop-login"
    assert api_paths.fall_back("https://nas.example", "/api/v1/laps/upload", resp) is False, "only once"
    assert api_paths.url_for("https://other.example", "/api/v1/laps/upload") == "https://other.example/api/v1/laps/upload"


def test_the_client_uploads_to_an_old_server_too(client, monkeypatch, tmp_path):
    """The real uploader against the real app over HTTP, which here plays a server before 1.0.2:
    /api/v1 answers 404 — the lap goes up on /telemetry/upload, and the next one at once there."""
    import requests

    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader
    from tests.local_servers import RealAppServer
    from tests.laps.test_upload_flow import _write_recorded_lap

    account = client.post("/api/v1/accounts/register", params={"display_name": "Old Server Driver"}).json()
    real_post = requests.post
    seen = []

    def old_server(url, *args, **kwargs):
        seen.append(url)
        if "/api/v1/" in url:
            return Resp(404)
        return real_post(url, *args, **kwargs)

    monkeypatch.setattr(requests, "post", old_server)
    server = RealAppServer().start()
    try:
        for n in (1, 2):
            folder = tmp_path / f"lap{n}"
            folder.mkdir()
            _write_recorded_lap(folder, lap_number=n, lap_time=90.0 + n)
            uploader = Uploader(recorder=LapRecorder(data_dir=str(folder)), backend_url=server.url,
                                auth_token=account["auth_token"], client_secret=account["client_secret"])
            assert uploader.run_once() == 1
    finally:
        server.stop()
    assert [u.rsplit("/", 2)[-2:] for u in seen] == [["laps", "upload"], ["telemetry", "upload"], ["telemetry", "upload"]]
