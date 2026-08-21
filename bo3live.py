"""bo3.gg live-detail client for the CS2 dashboard.

Liquipedia's ticker only publishes a series score between maps. For
in-match state - which map is on, round scores, official per-match
streams and team world ranks - this module talks to bo3.gg's public
API and push socket:

  REST  api.bo3.gg/api/v2/matches/live          -> which matches are on
        api.bo3.gg/api/v1/matches/<slug>?with=  -> teams, maps, streams
        api.bo3.gg/api/v1/live/matches/<id>/last_snapshot -> round data
  WS    wss://updates.bo3.gg/ws                 -> "something changed" pushes

Design: websocket events are only used as a trigger to re-poll REST,
so changes to the event payload schema can never break the dashboard.
Everything degrades gracefully - no live matches, a blocked socket or
API errors just mean the dashboard falls back to what Liquipedia has.
"""
import json
import os
import re
import threading
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "live_detail.json")
REST_TTL = 60           # full refresh at least once a minute when live
IDLE_TTL = 300          # when nothing is live, re-check every 5 min
FETCH_TIMEOUT = 10

API = "https://api.bo3.gg/api"
WS_URL = "wss://updates.bo3.gg/ws"
UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_lock = threading.Lock()
_state = {"updated": 0.0, "matches": []}
_wakeup = threading.Event()


def _get_json(url):
    return json.loads(fetch_bytes(url).decode())


def fetch_bytes(url):
    """Plain GET with a browser UA; returns response bytes."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return r.read()


# ------------------------------------------------------------------ REST --
def _local_date_and_offset():
    now = time.time()
    off = time.localtime(now).tm_gmtoff // 60  # minutes east of UTC
    date = time.strftime("%Y-%m-%d", time.localtime(now))
    return date, off


def fetch_live_list():
    date, off = _local_date_and_offset()
    url = (f"{API}/v2/matches/live?date={date}&utc_offset={off}"
           "&filter%5Bdiscipline_id%5D%5Beq%5D=1")
    data = _get_json(url)
    out = []
    for m in data.get("data", []):
        out.append({
            "id": m["id"],
            "slug": m["slug"],
            "series": [m.get("team1_score"), m.get("team2_score")],
            "bestof": m.get("bo_type"),
            "status": m.get("status"),
            "tier": m.get("tier"),
            "coverage": bool(m.get("live_coverage")),
            "team_ids": [m.get("team1_id"), m.get("team2_id")],
            "games": _norm_games(m.get("games")),
        })
    return out


def _norm_games(games):
    out = []
    for g in games or []:
        out.append({
            "number": g.get("number"),
            "status": g.get("status"),        # current / finished / upcoming
            "map": _map_name(g.get("map_name")),
        })
    out.sort(key=lambda g: g["number"] or 0)
    return out


def _map_name(raw):
    if not raw:
        return ""
    return re.sub(r"^de_", "", raw).replace("_", " ").title()


def fetch_match_detail(slug):
    url = (f"{API}/v1/matches/{slug}?scope=show-match&prefer_locale=en"
           "&with=games,teams,tournament_deep,stage")
    r = _get_json(url)
    r = r.get("results", r)

    def team(t):
        if not isinstance(t, dict):
            return {"name": "", "rank": None, "logo": ""}
        return {
            "name": t.get("name", ""),
            "rank": t.get("rank"),
            "logo": t.get("image_url", ""),
        }

    streams = []
    for s in r.get("streams") or []:
        if s.get("blocked"):
            continue
        emb = s.get("embed_url") or ""
        streams.append({
            "name": s.get("name", ""),
            "embed": emb,
            "url": s.get("raw_url", ""),
            "viewers": s.get("viewers_number"),
            "lang": s.get("language", ""),
            "provider": ("twitch" if "twitch" in emb else
                         "youtube" if "youtube" in emb else "other"),
        })
    streams.sort(key=lambda s: (s["provider"] == "other",
                                -(s["viewers"] or 0)))

    tour = r.get("tournament") or {}
    games = _norm_games(r.get("games"))
    # detail games lack map names for the current map - merge from maps list
    maps = r.get("match_maps") or []
    for i, g in enumerate(games):
        if not g["map"] and i < len(maps):
            g["map"] = _map_name(maps[i].get("map") or maps[i].get("map_name"))

    return {
        "teams": [team(r.get("team1")), team(r.get("team2"))],
        "streams": streams[:4],
        "games": games,
        "series": [r.get("team1_score"), r.get("team2_score")],
        "stage": (r.get("stage") or {}).get("name", ""),
        "tournament": {
            "name": tour.get("name", ""),
            "logo": tour.get("image_url", ""),
        },
    }


def fetch_snapshot(match_id):
    """Round-level snapshot; absent while bo3's coverage isn't producing."""
    try:
        url = f"{API}/v1/live/matches/{match_id}/last_snapshot?discipline_id=1"
        return _get_json(url)
    except Exception:
        return None


def refresh(limit=4):
    """Pull the full live picture and store it. Returns the new state."""
    global _state
    matches = fetch_live_list()[:limit]
    merged = []
    for m in matches:
        entry = dict(m)
        try:
            entry.update(fetch_match_detail(m["slug"]))
        except Exception:
            entry.setdefault("teams", [{"name": "", "rank": None, "logo": ""},
                                       {"name": "", "rank": None, "logo": ""}])
            entry.setdefault("streams", [])
        # series score: prefer whichever source is ahead (they lag differently)
        if entry.get("series") == [None, None]:
            entry["series"] = m["series"]
        if m["coverage"]:
            snap = fetch_snapshot(m["id"])
            if snap:
                entry["snapshot"] = snap
        merged.append(entry)
    state = {"updated": time.time(), "matches": merged}
    with _lock:
        _state = state
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass
    return state


def state(stale_ok=True):
    """Current live detail; refreshes in background when stale."""
    global _state
    with _lock:
        cur = _state
    now = time.time()
    n_live = len(cur.get("matches", []))
    if now - cur["updated"] < (REST_TTL if n_live else IDLE_TTL):
        return cur
    if cur["updated"] > 0 and stale_ok:
        threading.Thread(target=_refresh_quietly, daemon=True).start()
        return cur
    try:
        loaded = json.load(open(CACHE_FILE))
        if {"updated", "matches"} <= loaded.keys():
            with _lock:
                _state = loaded
            return loaded
    except (OSError, ValueError):
        pass
    try:
        return refresh()
    except Exception:
        return {"updated": 0.0, "matches": []}


def _refresh_quietly():
    try:
        refresh()
    except Exception:
        pass  # keep serving stale state; the next trigger retries


# -------------------------------------------------------------- matching --
def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def attach(liquipedia_live, bo3_state):
    """Attach bo3 detail to Liquipedia live matches (in place dicts).

    Teams are matched by normalised name; when both sides report exactly
    one live match we attach it regardless of naming differences.
    """
    bo3_matches = bo3_state.get("matches", [])
    for m in liquipedia_live:
        m["detail"] = None
    if not bo3_matches:
        return liquipedia_live
    if len(bo3_matches) == 1 and len(liquipedia_live) == 1:
        liquipedia_live[0]["detail"] = bo3_matches[0]
        return liquipedia_live
    used = set()
    for m in liquipedia_live:
        lp_names = {_norm(m["team1"]["name"]), _norm(m["team2"]["name"])}
        for i, b in enumerate(bo3_matches):
            if i in used:
                continue
            b_names = {_norm(t["name"]) for t in b.get("teams", [])}
            if b_names and (b_names & lp_names):
                m["detail"] = b
                used.add(i)
                break
    return liquipedia_live


# --------------------------------------------------------------------- WS --
def _ws_loop():
    """Keep a push socket open; any match event triggers a REST refresh."""
    try:
        import websockets
    except ImportError:
        return
    backoff = 5
    while True:
        try:
            import asyncio

            async def run():
                async with websockets.connect(
                        WS_URL,
                        additional_headers={"User-Agent": UA,
                                            "Origin": "https://bo3.gg"},
                        ping_interval=15) as ws:
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "payload": {"topics": [
                            {"key": "/matches", "type": "public"}]}}))
                    backoff_local = 5
                    while True:
                        # wake on push event OR every few minutes as backup
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=240)
                        except asyncio.TimeoutError:
                            _wakeup.set()
                            continue
                        if '"system"' not in msg:
                            _wakeup.set()
            asyncio.run(run())
            backoff = 5
        except Exception:
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


def start_background():
    """Start the push listener + periodic refresher (idempotent)."""
    if getattr(start_background, "_started", False):
        return
    start_background._started = True

    def refresher():
        while True:
            _wakeup.wait(timeout=IDLE_TTL)
            _wakeup.clear()
            try:
                refresh()
            except Exception:
                pass

    threading.Thread(target=refresher, daemon=True).start()
    threading.Thread(target=_ws_loop, daemon=True).start()
