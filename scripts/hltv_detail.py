#!/usr/bin/env python3
"""HLTV detail feeder for the CS2 dashboard.

Collects what neither Liquipedia nor bo3.gg publish - the map veto,
per-map results with half scores, and the running round score - from
HLTV match pages. HLTV blocks non-browsers, so this feeder renders
pages with camoufox (the stealth Firefox already on this host) and
writes hltv_detail.json for the web app to serve.

Runs as its own systemd unit (cs2-hltv.service) so a slow or stuck
browser can never affect the dashboard process: at worst the JSON goes
stale and the UI shows the other sources' data alone.

Cadence: while a match is live, refresh every ~90 s; when nothing is
live, close the browser and re-check every 5 min.
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/home/ev/cs2-dashboard")
import hltv  # noqa: E402

DASH_API = "http://127.0.0.1:8001/api/matches"
HLTV = "https://www.hltv.org"
STATE = "/home/ev/cs2-dashboard/hltv_detail.json"
LIVE_POLL = 90
IDLE_POLL = 300


def log(msg):
    print(f"[{time.strftime('%F %T')}] {msg}", flush=True)


def live_matches():
    try:
        with urllib.request.urlopen(DASH_API, timeout=10) as r:
            data = json.load(r)
        return data.get("live", [])
    except Exception:
        return []


class Browser:
    """Lazily-launched camoufox; a fresh context per page load."""

    def __init__(self):
        self._cm = None
        self._browser = None

    def _ensure(self):
        if self._browser is not None:
            return self._browser
        from camoufox.sync_api import Camoufox
        self._cm = Camoufox(headless="virtual", geoip=True)
        self._browser = self._cm.__enter__()
        return self._browser

    def html(self, url):
        page = self._ensure().new_page()
        try:
            page.goto(url, timeout=90000)
            page.wait_for_timeout(4000)
            return page.content()
        finally:
            page.close()

    def close(self):
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
        self._cm = None
        self._browser = None


def collect(browser, live):
    """Fetch veto/maps/round for the first live match HLTV also lists."""
    links_html = browser.html(HLTV + "/matches")
    links = hltv.find_live_match_links(links_html)
    for m in live:
        link = hltv.match_live_link(m, links)
        if not link:
            continue
        html = browser.html(HLTV + link["url"])
        scorebot = hltv.parse_scorebot(html)
        state = {
            "updated": time.time(),
            "url": HLTV + link["url"],
            "teams": link["teams"],
            "veto": hltv.parse_veto(html),
            "maps": hltv.parse_maps(html),
            "scorebot": scorebot,
            "round": scorebot["round"] if scorebot else None,
        }
        return state
    return None


def write(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    import os
    os.replace(tmp, STATE)


def main():
    browser = Browser()
    while True:
        try:
            live = live_matches()
            if not live:
                browser.close()
                time.sleep(IDLE_POLL)
                continue
            state = None
            try:
                state = collect(browser, live)
            except Exception as e:
                log(f"collect failed ({e}); restarting browser")
                browser.close()
            if state:
                write(state)
                r = state["round"]
                log(f"updated {state['teams']} maps={len(state['maps'])} "
                    f"round={r}")
            time.sleep(LIVE_POLL)
        except Exception as e:
            log(f"loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
