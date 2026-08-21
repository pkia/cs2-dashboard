#!/usr/bin/env python3
"""CS2 Dashboard - pro match ticker kiosk backend.

Serves live, upcoming and recent Counter-Strike 2 pro matches from
Liquipedia, with team logos and tournament icons cached locally so the
kiosk renders instantly and survives Liquipedia being slow or down.
"""
import hashlib
import os
import threading
import time

import bo3live
import hltv
import liquipedia
from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_DIR = os.path.join(BASE_DIR, "static", "logos")
ALLOWED_PREFIX = "/commons/images/"          # liquipedia, as ?path=
ALLOWED_IMG_HOSTS = ("https://files.bo3.gg/",
                    "https://image-proxy.bo3.gg/")  # bo3.gg, as ?url=
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

_logo_locks = {}
_logo_locks_guard = threading.Lock()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/matches")
def api_matches():
    state = liquipedia.matches()
    live = bo3live.attach(state["live"], bo3live.state())

    # HLTV layer (veto, map results, round score) comes from the
    # cs2-hltv feeder service; attach when fresh and teams line up.
    hs = hltv.read_state()
    if hs["updated"] and time.time() - hs["updated"] < 30 * 60 and live:
        hltv_names = {bo3live._norm(t) for t in hs.get("teams", [])}
        hltv_names.discard("")
        for m in live:
            lp_names = {bo3live._norm(m["team1"]["name"]),
                        bo3live._norm(m["team2"]["name"])}
            lp_names.discard("")
            if hltv_names & lp_names:
                m["hltv"] = hs
                # align the running round score to team1/team2 order using
                # the scorebot's CT/T side team names
                sb = hs.get("scorebot") or {}
                pair = (sb or {}).get("round") or hs.get("round")
                if pair:
                    pair = list(pair)
                    lp1 = bo3live._norm(m["team1"]["name"])
                    if bo3live._norm(sb.get("t_team", "")) == lp1:
                        pair = [pair[1], pair[0]]  # team1 plays the T side
                    m["round"] = pair
                    m["round_info"] = {
                        "num": sb.get("round_num"),
                        "map": sb.get("map"),
                        "timer": sb.get("timer"),
                    }
                break
    return jsonify({
        "ok": state["fetched_at"] > 0,
        "fetched_at": state["fetched_at"],
        "live": live,
        "upcoming": state["upcoming"],
        "recent": state["recent"],
        "time": time.time(),
    })


@app.route("/api/live")
def api_live():
    state = bo3live.state()
    return jsonify(state)


@app.route("/api/status")
def api_status():
    age = liquipedia.last_fetch_age()
    return jsonify({
        "ok": True,
        "service": "cs2-dashboard",
        "last_fetch_age": round(age, 1) if age is not None else None,
        "data_source": "liquipedia.net",
    })


@app.route("/api/logo")
def api_logo():
    """Proxy and cache one remote image (Liquipedia or bo3.gg hosts).

    The dashboard asks for /api/logo?path=<liquipedia path> or
    ?url=<full bo3.gg image URL>; the first request fetches it and later
    ones are served from disk. Only the two allow-listed sources with an
    image extension are ever fetched, so this cannot be used as an open
    proxy.
    """
    path = request.args.get("path", "")
    url = request.args.get("url", "")
    if not path and url:
        if not url.startswith(ALLOWED_IMG_HOSTS) or ".." in url:
            return jsonify({"error": "url not allowed"}), 400
        path = url
    ext = os.path.splitext(path)[1].lower()
    if not path.startswith(ALLOWED_PREFIX) and not path.startswith(ALLOWED_IMG_HOSTS):
        return jsonify({"error": "path not allowed"}), 400
    if ext not in ALLOWED_EXT or ".." in path:
        return jsonify({"error": "path not allowed"}), 400
    if path.startswith(ALLOWED_PREFIX):
        fetch_url = liquipedia.ROOT + path
    else:
        fetch_url = path

    name = hashlib.sha256(path.encode()).hexdigest()[:24] + ext
    cache_file = os.path.join(LOGO_DIR, name)
    if not os.path.exists(cache_file):
        os.makedirs(LOGO_DIR, exist_ok=True)
        with _logo_locks_guard:
            lock = _logo_locks.setdefault(name, threading.Lock())
        with lock:
            if not os.path.exists(cache_file):
                try:
                    data = bo3live.fetch_bytes(fetch_url)
                    tmp = cache_file + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(data)
                    os.replace(tmp, cache_file)
                except Exception:
                    return jsonify({"error": "fetch failed"}), 502
    return send_from_directory(LOGO_DIR, name, max_age=86400)


if __name__ == "__main__":
    os.makedirs(LOGO_DIR, exist_ok=True)
    threading.Thread(target=liquipedia.matches, daemon=True).start()
    bo3live.start_background()
    app.run(host="0.0.0.0", port=8001, threaded=True)
