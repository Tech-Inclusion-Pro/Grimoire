"""Auth tests.

The failure that matters is not "login rejects a bad password" -- it is any
path that silently serves the collection to an unauthenticated request on a
tailnet shared with other people.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from backend import auth, db, scryfall

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scryfall_cards.json"
PASSWORD = "correct horse battery staple"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    # auth.CONFIG_PATH is already pointed at tmp_path by conftest.
    monkeypatch.setenv("GRIMOIRE_DB", str(tmp_path / "auth.db"))
    conn = db.open_db(tmp_path / "auth.db")
    scryfall.load_cards(conn, json.loads(FIXTURES.read_text(encoding="utf-8")))
    conn.close()
    return tmp_path


@pytest.fixture()
def client(env):
    from backend.main import app
    with TestClient(app) as c:
        yield c


def configure(username: str = "rocco", **extra) -> None:
    auth.save_auth_config({
        "enabled": True,
        "username": username,
        "password_hash": auth.hash_password(PASSWORD),
        "session_secret": "0" * 64,
        **extra,
    })


# --- hashing ---------------------------------------------------------------

def test_password_is_never_stored_in_plaintext(env):
    configure()
    raw = (env / "auth.json").read_text(encoding="utf-8")
    assert PASSWORD not in raw
    assert "pbkdf2_sha256$" in raw


def test_hashes_are_salted(env):
    assert auth.hash_password(PASSWORD) != auth.hash_password(PASSWORD)
    assert auth.verify_password(PASSWORD, auth.hash_password(PASSWORD))


def test_verify_rejects_malformed_input_without_raising(env):
    for bad in ("", "nonsense", "pbkdf2_sha256$notanint$a$b", None):
        assert auth.verify_password(PASSWORD, bad) is False  # type: ignore[arg-type]


def test_config_file_is_not_world_readable(env):
    configure()
    assert (env / "auth.json").stat().st_mode & 0o077 == 0


# --- cookies ---------------------------------------------------------------

def test_cookie_signature_cannot_be_forged(env):
    good = auth.issue_cookie("rocco", "secret-a")
    assert auth.verify_cookie(good, "secret-a") == "rocco"
    assert auth.verify_cookie(good, "secret-b") is None, "signed with a different key"

    payload, sig = good.split(".", 1)
    tampered = auth._b64url_encode(b'{"u":"someone-else","iat":0}')
    assert auth.verify_cookie(f"{tampered}.{sig}", "secret-a") is None


def test_expired_cookie_is_rejected_when_a_lifetime_is_set(env, monkeypatch):
    token = auth.issue_cookie("rocco", "s")
    assert auth.verify_cookie(token, "s", max_age_days=30) == "rocco"

    # Capture the real clock before patching, or the replacement calls itself.
    later = auth.time.time() + 31 * 86400
    monkeypatch.setattr(auth.time, "time", lambda: later)
    assert auth.verify_cookie(token, "s", max_age_days=30) is None
    assert auth.verify_cookie(token, "s") == "rocco", "no lifetime set means no expiry"


# --- the endpoints ---------------------------------------------------------

def test_collection_is_unreachable_without_logging_in(client, env):
    configure()
    for path in ("/api/stats", "/api/cards?q=Llanowar&owned=false",
                 "/api/collection", "/api/locations"):
        assert client.get(path).status_code == 401, f"{path} leaked"


def test_login_then_read(client, env):
    configure()
    assert client.post("/api/auth/login",
                       json={"username": "rocco", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"username": "nobody", "password": PASSWORD}).status_code == 401

    ok = client.post("/api/auth/login", json={"username": "rocco", "password": PASSWORD})
    assert ok.status_code == 200
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "rocco"

    client.post("/api/auth/logout")
    assert client.get("/api/stats").status_code == 401


def test_login_page_and_its_assets_stay_reachable(client, env):
    configure()
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/auth/status").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_status_does_not_leak_whether_a_password_is_right(client, env):
    configure()
    body = client.get("/api/auth/status").json()
    assert body == {"enabled": True, "authenticated": False, "username": None}


def test_repeated_failures_are_throttled(client, env):
    configure()
    codes = [client.post("/api/auth/login",
                         json={"username": "rocco", "password": "no"}).status_code
             for _ in range(12)]
    assert 429 in codes, "unthrottled login turns a weak password into a short wait"


def test_misconfigured_auth_fails_closed(client, env):
    """A half-written config must not degrade into 'no auth' on a shared tailnet."""
    (env / "auth.json").write_text('{"enabled": true, "username": "rocco"}', encoding="utf-8")
    assert client.get("/api/stats").status_code == 503


def test_disabled_auth_is_reported_honestly(client, env):
    configure()
    cfg, _, _ = auth.load_auth_config()
    cfg["enabled"] = False
    auth.save_auth_config(cfg)
    body = client.get("/api/auth/status").json()
    assert body["enabled"] is False
    assert client.get("/api/stats").status_code == 200
