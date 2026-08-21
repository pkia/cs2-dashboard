#!/usr/bin/env python3
"""HLTV detail feeder for the CS2 dashboard.

Collects what neither Liquipedia nor bo3.gg publish - the map veto,
per-map results with half scores, and the running round score - from
HLTV match pages. HLTV blocks non-browsers, so this feeder renders
pages with camoufox (the stealth Firefox already on this host) and
writes hltv_detail.json for the web app to serve.

Freshness design: a full page render (veto, map results) only happens
when the tracked match changes and every few minutes after that. In
between, the SAME page stays open and just the scorebot numbers are
read out of the live DOM every few seconds - the page maintains HLTV's
scorebot websocket itself, so the round score lands within seconds of
changing, at a fraction of the cost of re-rendering.

Runs as its own systemd unit (cs2-hltv.service) so a slow or stuck
browser can never affect the dashboard process.
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
SCORE_READ = 8          # seconds between cheap DOM score reads
FULL_REFRESH = 300      # seconds between full page re-renders
IDLE_POLL = 300

SCORE_JS = """() => {
    const q = (s) => document.querySelector(s);
    const txt = (s) => { const e = q(s); return e ? e.textContent.trim() : null; };
    const out = {round: null, round_num: null, map: '', timer: '',
                 ct_team: '', t_team: ''};
    const ct = txt('.scorebot .ctScore'), t = txt('.scorebot .tScore');
    if (ct !== null && t !== null) out.round = [parseInt(ct), parseInt(t)];
    const rt = txt('.scorebot .currentRoundText');
    if (rt) { const m = rt.match(/(\\d+)\\s*-\\s*(.+)/);
              if (m) { out.round_num = +m[1]; out.map = m[2].trim(); } }
    const teams = [...document.querySelectorAll('.scorebot table.team .teamName')]
        .map((e) => e.textContent.trim());
    out.ct_team = teams[0] || '';
    out.t_team = teams[1] || '';
    return out;
}"""


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
    """Lazily-launched camoufox; one persistent page while tracking."""

    def __init__(self):
        self._cm = None
        self._browser = None
        self.page = None

    def _ensure(self):
        if self._browser is not None:
            return self._browser
        from camoufox.sync_api import Camoufox
        self._cm = Camoufox(headless="virtual", geoip=True)
        self._browser = self._cm.__enter__()
        return self._browser

    def open(self, url, wait_sel=None):
        if self.page is not None:
            try:
                self.page.close()
            except Exception:
                pass
        self.page = self._ensure().new_page()
        self.page.goto(url, timeout=90000)
        if wait_sel:
            try:
                # the live scorebot renders asynchronously; racing it with
                # a fixed sleep misses it on warm (cached) loads
                self.page.wait_for_selector(wait_sel, timeout=14000)
            except Exception:
                pass  # e.g. the break between maps

    def read_score(self):
        return self.page.evaluate(SCORE_JS)

    def content(self):
        return self.page.content()

    def close(self):
        self.page = None
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
        self._cm = None
        self._browser = None


def _norm(name):
    return "".join(c for c in (name or "").lower() if c.isalnum())


def resolve_target(browser, live):
    """Find the HLTV live-match link for one of our live matches."""
    links_html = fetch_once(browser, HLTV + "/matches")
    links = hltv.find_live_match_links(links_html)
    for m in live:
        link = hltv.match_live_link(m, links)
        if link:
            return link, m
    return None, None


def fetch_once(browser, url, wait_sel=None):
    """One-off page load returning its HTML (used for the matches list)."""
    page = browser._ensure().new_page()
    try:
        page.goto(url, timeout=90000)
        page.wait_for_timeout(3500)
        return page.content()
    finally:
        page.close()


def full_refresh(browser, live, link):
    """Render the match page and parse everything (veto, maps, score)."""
    in_progress = any(g.get("status") == "current"
                      for g in ((live.get("detail") or {}).get("games") or []))
    browser.open(HLTV + link["url"],
                 wait_sel=".scorebot .scoreText" if in_progress else None)
    html = browser.content()
    scorebot = hltv.parse_scorebot(html)
    return {
        "url": HLTV + link["url"],
        "teams": link["teams"],
        "veto": hltv.parse_veto(html),
        "maps": hltv.parse_maps(html),
        "scorebot": scorebot,
        "round": scorebot["round"] if scorebot else None,
    }


def write(state):
    state = dict(state, updated=time.time())
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    import os
    os.replace(tmp, STATE)
    return state


def main():
    browser = Browser()
    state = {}          # last written state (minus updated)
    target = None       # link dict of the tracked match
    target_teams = set()
    last_full = 0.0

    while True:
        try:
            live = live_matches()
            if not live:
                target, state = None, {}
                browser.close()
                time.sleep(IDLE_POLL)
                continue

            # (re)resolve which match to track
            if target is not None:
                lp_names = {_norm(t["name"]) for m in live for t in
                            (m["team1"], m["team2"])}
                if not (target_teams & lp_names):
                    target = None  # tracked match no longer live
            if target is None:
                target, m = resolve_target(browser, live)
                if target is None:
                    browser.close()
                    time.sleep(60)
                    continue
                target_teams = {_norm(t) for t in target["teams"]}
                state = full_refresh(browser, m, target)
                state = write(state)
                last_full = time.time()
                log(f"tracking {target['teams']} round={state['round']}")
                time.sleep(SCORE_READ)
                continue

            # cheap refresh: score from the open page's live DOM
            if time.time() - last_full > FULL_REFRESH:
                m = next((x for x in live if target_teams & {
                    _norm(x["team1"]["name"]), _norm(x["team2"]["name"])}), live[0])
                state = full_refresh(browser, m, target)
                state = write(state)
                last_full = time.time()
                log(f"full refresh round={state['round']}")
            else:
                try:
                    sb = browser.read_score()
                except Exception:
                    sb = None
                    # page died - reopen it
                    m = live[0]
                    state = full_refresh(browser, m, target)
                    state = write(state)
                    last_full = time.time()
                if sb is not None:
                    if sb.get("round") is not None:
                        state["scorebot"] = sb
                        state["round"] = sb["round"]
                    else:
                        state["scorebot"] = None   # between maps
                        state["round"] = None
                    state = write(state)
            time.sleep(SCORE_READ)
        except Exception as e:
            log(f"loop error: {e}")
            try:
                browser.close()
            except Exception:
                pass
            target = None
            time.sleep(30)


if __name__ == "__main__":
    main()
