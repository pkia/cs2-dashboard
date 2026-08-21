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

import liquipedia
from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_DIR = os.path.join(BASE_DIR, "static", "logos")
ALLOWED_PREFIX = "/commons/images/"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

_logo_locks = {}
_logo_locks_guard = threading.Lock()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/matches")
def api_matches():
    state = liquipedia.matches()
    return jsonify({
        "ok": state["fetched_at"] > 0,
        "fetched_at": state["fetched_at"],
        "live": state["live"],
        "upcoming": state["upcoming"],
        "recent": state["recent"],
        "time": time.time(),
    })


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
    """Proxy and cache one Liquipedia image.

    The dashboard always asks for /api/logo?path=<image path>; the first
    request fetches it from Liquipedia and later ones are served from
    disk. Only paths under /commons/images/ with an image extension are
    ever fetched, so the endpoint cannot be used as an open proxy.
    """
    path = request.args.get("path", "")
    ext = os.path.splitext(path)[1].lower()
    if not path.startswith(ALLOWED_PREFIX) or ext not in ALLOWED_EXT or ".." in path:
        return jsonify({"error": "path not allowed"}), 400

    name = hashlib.sha256(path.encode()).hexdigest()[:24] + ext
    cache_file = os.path.join(LOGO_DIR, name)
    if not os.path.exists(cache_file):
        with _logo_locks_guard:
            lock = _logo_locks.setdefault(name, threading.Lock())
        with lock:
            if not os.path.exists(cache_file):
                try:
                    data = liquipedia.fetch(liquipedia.ROOT + path, timeout=10)
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
    app.run(host="0.0.0.0", port=8001, threaded=True)
