import json
import os

import app
import liquipedia

client = app.app.test_client()

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "matches.html")


def test_index_serves_page():
    r = client.get("/")
    assert r.status_code == 200
    assert b"CS2 Pro Matches" in r.data


def test_api_status():
    j = client.get("/api/status").get_json()
    assert j["ok"] is True
    assert j["service"] == "cs2-dashboard"


def test_api_matches_with_data(monkeypatch):
    state = liquipedia.parse_matches(open(FIXTURE).read())
    state["fetched_at"] = 123.0
    monkeypatch.setattr(liquipedia, "matches", lambda: state)
    j = client.get("/api/matches").get_json()
    assert j["ok"] is True
    assert j["fetched_at"] == 123.0
    assert len(j["live"]) == 1
    assert len(j["upcoming"]) == 2
    assert len(j["recent"]) == 3
    assert j["recent"][0]["team1"]["name"] == "FUT"


def test_api_matches_degrades_when_never_fetched(monkeypatch):
    monkeypatch.setattr(liquipedia, "matches", lambda: {
        "fetched_at": 0.0, "live": [], "upcoming": [], "recent": []})
    j = client.get("/api/matches").get_json()
    assert j["ok"] is False
    assert j["live"] == []


def test_logo_proxy_rejects_non_liquipedia_paths():
    for bad in ["http://evil.com/x.png", "/etc/passwd", "../secret.png",
                "/commons/../etc/passwd.png", "/commons/images/x.php", ""]:
        r = client.get("/api/logo?path=" + bad)
        assert r.status_code == 400, bad


def test_logo_proxy_fetches_and_caches(monkeypatch, tmp_path):
    app.LOGO_DIR = str(tmp_path)
    monkeypatch.setattr(liquipedia, "fetch",
                        lambda url, timeout=10: b"\x89PNG-fake")
    r = client.get("/api/logo?path=/commons/images/a/b/logo.png")
    assert r.status_code == 200
    assert r.data == b"\x89PNG-fake"
    # second hit is served from the disk cache with no fetch attempted
    monkeypatch.setattr(liquipedia, "fetch",
                        lambda url, timeout=10: (_ for _ in ()).throw(AssertionError()))
    r = client.get("/api/logo?path=/commons/images/a/b/logo.png")
    assert r.status_code == 200


def test_logo_cache_survives_fetch_failure(monkeypatch, tmp_path):
    app.LOGO_DIR = str(tmp_path)
    monkeypatch.setattr(liquipedia, "fetch",
                        lambda url, timeout=10: (_ for _ in ()).throw(OSError()))
    assert client.get("/api/logo?path=/commons/images/c/d/other.jpg").status_code == 502


def test_disk_cache_roundtrip(tmp_path, monkeypatch):
    """A restart reloades cache.json instead of fetching inline."""
    cache = os.path.join(tmp_path, "cache.json")
    monkeypatch.setattr(liquipedia, "CACHE_FILE", cache)
    state = {"fetched_at": 9999999999.0, "live": [], "upcoming": [], "recent": []}
    with open(cache, "w") as f:
        json.dump(state, f)
    # reset in-memory cache to simulate a fresh process
    monkeypatch.setattr(liquipedia, "_cache", {"fetched_at": 0.0, "live": [], "upcoming": [], "recent": []})
    got = liquipedia.matches(stale_ok=False)
    assert got["fetched_at"] == 9999999999.0
