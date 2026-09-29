"""Tracks grouped by venue with all layouts, searchable (V0.8.9): every known
track exists before its first lap, layouts appear by themselves from real
laps, unknown tracks are learned, and searching "fuji" finds the whole track."""

from __future__ import annotations

import pytest

from server import catalog


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _upload(client, account, track, car_class="GT3", car_model="car_a", lap_time=90.0):
    from tests.test_catalog import _upload as upload

    return upload(client, account, track, "Car #1", car_class, car_model, lap_time=lap_time)


def _venues(client, **params):
    resp = client.get("/leaderboard/catalog/venues", params=params)
    assert resp.status_code == 200
    return {v["venue"]: v for v in resp.json()}


# ------------------------------------------------ grouping (pure functions)

@pytest.mark.parametrize(
    "raw,venue",
    [
        ("Fuji Speedway", "Fuji Speedway"),
        ("Fuji Speedway - Classic", "Fuji Speedway"),
        ("FUJI_SPEEDWAY_GP", "Fuji Speedway"),
        ("Circuit de la Sarthe", "Circuit de la Sarthe (Le Mans)"),
        ("Le Mans 24h", "Circuit de la Sarthe (Le Mans)"),
        ("Spa-Francorchamps Endurance", "Spa-Francorchamps"),
        ("Autodromo Enzo e Dino Ferrari", "Imola (Enzo e Dino Ferrari)"),
        ("Autódromo Internacional do Algarve", "Portimão (Algarve)"),
        ("Circuit of the Americas Grand Prix", "Circuit of the Americas"),
        ("COTA", "Circuit of the Americas"),
    ],
)
def test_layout_strings_group_under_their_venue(raw, venue):
    assert catalog.venue_for(raw) == venue


def test_a_short_keyword_must_be_a_whole_word_so_spa_does_not_match_espanya():
    assert catalog.venue_for("Circuit de Barcelona-Catalunya España") != "Spa-Francorchamps"
    assert catalog.venue_for("Espanya Circuit") == "Espanya Circuit"


def test_an_unknown_track_is_its_own_venue_spelled_as_sent():
    assert catalog.venue_for("Nordschleife Touristenfahrten") == "Nordschleife Touristenfahrten"


# ------------------------------------------------------------ the endpoint

def test_every_known_track_is_listed_before_a_single_lap_exists(client):
    venues = _venues(client)
    assert set(catalog.known_venue_names()) <= set(venues)
    assert all(v["layouts"] == [] and v["lap_count"] == 0 for v in venues.values())
    assert "Fuji Speedway" in venues


def test_layouts_appear_by_themselves_from_real_laps(client):
    account = _register(client)
    _upload(client, account, "Fuji Speedway", car_model="a")
    _upload(client, account, "Fuji Speedway - Classic", car_model="b", lap_time=95.0)
    _upload(client, account, "Fuji Speedway - Classic", car_model="c", lap_time=96.0)
    fuji = _venues(client)["Fuji Speedway"]
    assert {(l["track_name"], l["lap_count"]) for l in fuji["layouts"]} == {("Fuji Speedway", 1), ("Fuji Speedway - Classic", 2)}
    assert fuji["lap_count"] == 3


def test_an_unknown_track_shows_up_automatically(client):
    account = _register(client)
    _upload(client, account, "Brand New Circuit")
    venue = _venues(client)["Brand New Circuit"]
    assert [l["track_name"] for l in venue["layouts"]] == ["Brand New Circuit"]


def test_searching_fuji_returns_the_whole_track_with_every_variant(client):
    account = _register(client)
    _upload(client, account, "Fuji Speedway", car_model="a")
    _upload(client, account, "Fuji Speedway - Classic", car_model="b")
    _upload(client, account, "Monza", car_model="c")
    for query in ("fuji", "FUJI", "Fuji Speed", "  fuji "):
        found = _venues(client, q=query)
        assert list(found) == ["Fuji Speedway"], query
        assert len(found["Fuji Speedway"]["layouts"]) == 2


@pytest.mark.parametrize(
    "query,venue",
    [("le mans", "Circuit de la Sarthe (Le Mans)"), ("sarthe", "Circuit de la Sarthe (Le Mans)"), ("portimao", "Portimão (Algarve)"),
     ("PORTIMÃO", "Portimão (Algarve)"), ("spa", "Spa-Francorchamps"), ("americas", "Circuit of the Americas"), ("imola", "Imola (Enzo e Dino Ferrari)")],
)
def test_search_ignores_case_accents_and_spaces_and_finds_known_tracks_with_no_laps(client, query, venue):
    assert list(_venues(client, q=query)) == [venue]


def test_search_also_matches_layout_names_and_returns_nothing_for_gibberish(client):
    account = _register(client)
    _upload(client, account, "Fuji Speedway - Classic")
    assert list(_venues(client, q="classic")) == ["Fuji Speedway"]
    assert _venues(client, q="zzzzqqq") == {}


def test_only_valid_laps_count(client):
    from server.database import SessionLocal
    from server.models import Lap

    account = _register(client)
    _upload(client, account, "Monza")
    db = SessionLocal()
    try:
        db.query(Lap).update({"is_valid": False})
        db.commit()
    finally:
        db.close()
    assert _venues(client)["Monza"]["lap_count"] == 0


def test_the_catalog_is_public_like_the_leaderboard(client):
    assert client.get("/leaderboard/catalog/venues").status_code == 200
    assert client.get("/leaderboard/catalog/class-options").status_code == 200


# --------------------------------------------- "find my laps" is a search too

def test_my_laps_filter_by_track_is_a_case_insensitive_venue_search(client):
    account = _register(client)
    headers = {"Authorization": f"Bearer {account['auth_token']}"}
    _upload(client, account, "Fuji Speedway", car_model="a")
    _upload(client, account, "Fuji Speedway - Classic", car_model="b", lap_time=95.0)
    _upload(client, account, "Monza", car_model="c", lap_time=99.0)

    def tracks(q):
        return sorted(l["track_name"] for l in client.get("/telemetry/laps", params={"track_name": q}, headers=headers).json())

    assert tracks("fuji") == ["Fuji Speedway", "Fuji Speedway - Classic"]
    assert tracks("FUJI SPEEDWAY - CLASSIC") == ["Fuji Speedway - Classic"] or tracks("Fuji Speedway - Classic") == ["Fuji Speedway - Classic"]
    assert tracks("Monza") == ["Monza"]
    assert tracks("nothing-like-this") == []
    assert len(client.get("/telemetry/laps", headers=headers).json()) == 3  # no filter: everything
