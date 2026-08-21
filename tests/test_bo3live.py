import time

import pytest

import bo3live

import pytest


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(bo3live, "_last_good_detail", {})
    monkeypatch.setattr(bo3live, "_last_refresh", 0.0)
    yield


# Captured (lightly trimmed) shapes of bo3.gg's public API responses.
LIVE_LIST = {
    "data": [{
        "id": 126438,
        "slug": "legacy-br-vs-falcons-esports-21-08-2026",
        "status": "current",
        "bo_type": 3,
        "tier": "s",
        "live_coverage": True,
        "team1_id": 8118, "team2_id": 2713,
        "team1_score": 1, "team2_score": 0,
        "games": [
            {"number": 3, "status": "upcoming"},
            {"number": 1, "status": "finished", "map_name": "de_mirage"},
            {"number": 2, "status": "current", "map_name": "de_inferno"},
        ],
    }]
}

DETAIL = {
    "results": {
        "team1_score": 1, "team2_score": 0,
        "team1": {"id": 8118, "name": "Legacy", "rank": 7,
                  "image_url": "https://files.bo3.gg/uploads/team/8118/image/x.webp"},
        "team2": {"id": 2713, "name": "Falcons", "rank": 2,
                  "image_url": "https://files.bo3.gg/uploads/team/2713/image/y.webp"},
        "stage": {"name": "Quarterfinal"},
        "tournament": {"name": "Esports World Cup 2026", "image_url": ""},
        "games": [
            {"id": 1, "status": "finished", "number": 1},
            {"id": 2, "status": "current", "number": 2},
            {"id": 3, "status": "upcoming", "number": 3},
        ],
        "match_maps": [
            {"map": "de_mirage", "score1": 13, "score2": 7},
            {"map": "de_inferno", "score1": 6, "score2": 8},
        ],
        "streams": [
            {"name": "EWC_Plus_EN", "embed_url": "https://player.twitch.tv/?channel=ewc_plus_en",
             "raw_url": "https://www.twitch.tv/ewc_plus_en", "viewers_number": 4200,
             "language": "en", "blocked": False},
            {"name": "cs2_maincast", "embed_url": "https://player.twitch.tv/?channel=cs2_maincast",
             "raw_url": "https://www.twitch.tv/cs2_maincast", "viewers_number": 300,
             "language": "ua", "blocked": False},
            {"name": "blockedone", "embed_url": "https://player.twitch.tv/?channel=nope",
             "raw_url": "x", "viewers_number": 1, "language": "en", "blocked": True},
        ],
    }
}


def test_live_list_normalises(monkeypatch):
    monkeypatch.setattr(bo3live, "_get_json", lambda url: LIVE_LIST)
    out = bo3live.fetch_live_list()
    assert len(out) == 1
    m = out[0]
    assert m["id"] == 126438
    assert m["series"] == [1, 0]
    assert m["coverage"] is True
    assert [g["number"] for g in m["games"]] == [1, 2, 3]   # sorted
    assert m["games"][0]["map"] == "Mirage"                  # de_ stripped


def test_detail_normalises_streams_and_maps(monkeypatch):
    def fake_get(url):
        return DETAIL if "scope=show-match" in url else LIVE_LIST
    monkeypatch.setattr(bo3live, "_get_json", fake_get)
    monkeypatch.setattr(bo3live, "fetch_snapshot",
                        lambda mid: {"data": {"score_team1": 6, "score_team2": 8}})
    state = bo3live.refresh()
    m = state["matches"][0]
    assert m["teams"][0]["name"] == "Legacy"
    assert m["teams"][0]["rank"] == 7
    # blocked stream filtered, viewers sorted first
    assert [s["name"] for s in m["streams"]] == ["EWC_Plus_EN", "cs2_maincast"]
    # map names merged in from match_maps for games lacking them
    assert m["games"][0]["map"] == "Mirage"
    assert m["games"][1]["map"] == "Inferno"
    assert m["snapshot"]["data"]["score_team1"] == 6


def test_snapshot_absent_is_fine(monkeypatch):
    def fake_get(url):
        return DETAIL if "scope=show-match" in url else LIVE_LIST
    monkeypatch.setattr(bo3live, "_get_json", fake_get)
    monkeypatch.setattr(bo3live, "fetch_snapshot", lambda mid: None)
    state = bo3live.refresh()
    assert "snapshot" not in state["matches"][0]


def test_detail_failure_degrades(monkeypatch):
    def fake_get(url):
        if "scope=show-match" in url:
            raise OSError("boom")
        return LIVE_LIST
    monkeypatch.setattr(bo3live, "_get_json", fake_get)
    monkeypatch.setattr(bo3live, "fetch_snapshot", lambda mid: None)
    state = bo3live.refresh()
    m = state["matches"][0]
    assert m["series"] == [1, 0]  # v2 score survives
    assert m["streams"] == []


def test_attach_by_team_name():
    lp = [{
        "team1": {"name": "Legacy"}, "team2": {"name": "Falcons"},
        "detail": None,
    }]
    bo3 = {"matches": [{"teams": [{"name": "Legacy"}, {"name": "Falcons"}]}]}
    bo3live.attach(lp, bo3)
    assert lp[0]["detail"] is bo3["matches"][0]


def test_attach_single_live_fallback():
    lp = [{"team1": {"name": "Totally Different"}, "team2": {"name": "Names"},
           "detail": None}]
    bo3 = {"matches": [{"teams": [{"name": "Whatever"}, {"name": "Else"}]}]}
    bo3live.attach(lp, bo3)
    assert lp[0]["detail"] is not None


def test_attach_no_bo3_data():
    lp = [{"team1": {"name": "A"}, "team2": {"name": "B"}, "detail": None}]
    bo3live.attach(lp, {"matches": []})
    assert lp[0]["detail"] is None


def test_state_serves_stale_when_fresh(monkeypatch):
    fresh = {"updated": time.time(), "matches": [{"slug": "x"}]}
    monkeypatch.setattr(bo3live, "_state", fresh)
    monkeypatch.setattr(bo3live, "refresh",
                        lambda limit=4: (_ for _ in ()).throw(AssertionError()))
    assert bo3live.state() is fresh


def test_refresh_keeps_last_good_detail(monkeypatch):
    """A transient detail failure must not clobber cached teams/streams."""
    live = LIVE_LIST
    detail_calls = {"n": 0}

    def fake_get(url):
        if "scope=show-match" in url:
            detail_calls["n"] += 1
            if detail_calls["n"] == 1:
                return DETAIL
            raise OSError("transient")
        return live

    monkeypatch.setattr(bo3live, "_get_json", fake_get)
    monkeypatch.setattr(bo3live, "fetch_snapshot", lambda mid: None)
    monkeypatch.setattr(bo3live, "_last_refresh", 0.0)
    first = bo3live.refresh(force=True)
    assert first["matches"][0]["teams"][0]["name"] == "Legacy"
    assert len(first["matches"][0]["streams"]) == 2
    second = bo3live.refresh(force=True)   # detail fetch now fails
    m = second["matches"][0]
    assert m["teams"][0]["name"] == "Legacy"      # kept
    assert len(m["streams"]) == 2                  # kept
    assert m["series"] == [1, 0]                   # fresh from v2


def test_refresh_coalesces_bursts(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(bo3live, "fetch_live_list",
                        lambda: (calls.__setitem__("n", calls["n"] + 1) or []))
    monkeypatch.setattr(bo3live, "_last_refresh", time.time())
    bo3live.refresh()          # within the gap -> no fetch
    bo3live.refresh(force=True)
    assert calls["n"] == 1
