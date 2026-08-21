"""Liquipedia match ticker client for the CS2 dashboard.

Fetches the Counter-Strike wiki's Liquipedia:Matches page through the
public parse API - one request returns live, upcoming and recently
completed pro matches - and turns the HTML into structured match dicts.

API terms of use: gzip-encoded requests, a descriptive User-Agent and at
most one request per two seconds. This module makes a single request per
refresh and the dashboard refreshes at most once every two minutes, well
inside the limits.
"""
import gzip
import json
import os
import re
import threading
import time
import urllib.request
from html.parser import HTMLParser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "cache.json")
CACHE_TTL = 120  # seconds a fetch is considered fresh
FETCH_TIMEOUT = 15

API_URL = ("https://liquipedia.net/counterstrike/api.php"
           "?action=parse&page=Liquipedia:Matches&format=json")
USER_AGENT = "dunbot-cs2-dashboard/1.0 (personal Raspberry Pi kiosk; github.com/pkia)"
ROOT = "https://liquipedia.net"

# ------------------------------------------------------------------ dom --
class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []  # Node or str

    # -- queries ---------------------------------------------------------
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def has_class(self, *names):
        c = self.classes()
        return any(n in c for n in names)

    def walk(self):
        for ch in self.children:
            if isinstance(ch, Node):
                yield ch
                yield from ch.walk()

    def find(self, tag=None, cls=None):
        for n in self.walk():
            if tag and n.tag != tag:
                continue
            if cls and not n.has_class(cls):
                continue
            return n
        return None

    def find_all(self, tag=None, cls=None):
        return [n for n in self.walk()
                if (not tag or n.tag == tag) and (not cls or n.has_class(cls))]

    def text(self):
        parts = []

        def collect(n):
            for ch in n.children:
                if isinstance(ch, str):
                    parts.append(ch)
                else:
                    collect(ch)

        collect(self)
        return re.sub(r"\s+", " ", "".join(parts)).strip()


class TreeBuilder(HTMLParser):
    """Builds a Node tree; tolerates Liquipedia's occasional void tags."""

    VOID = {"img", "br", "hr", "input", "link", "meta"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self._stack[-1].children.append(node)
        if tag not in self.VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        if data.strip():
            self._stack[-1].children.append(data)


def parse_html(html):
    b = TreeBuilder()
    b.feed(html)
    b.close()
    return b.root


# -------------------------------------------------------------- matches --
def _match_blocks(html):
    """Yield the HTML of each top-level <div class="match-info"> block."""
    out, pos = [], 0
    open_re = re.compile(r'<div\b[^>]*>')
    for m in open_re.finditer(html, pos):
        start = m.start()
        cls = re.search(r'class="([^"]*)"', m.group(0))
        if not cls or cls.group(1).split() != ["match-info"]:
            continue
        depth, i = 0, start
        for t in re.finditer(r"<div\b[^>]*>|</div>", html[start:]):
            depth += 1 if t.group(0).startswith("<div") else -1
            if depth == 0:
                i = start + t.end()
                break
        if i > start:
            out.append(html[start:i])
    return out


def _stream_url(href):
    """Special:Stream/twitch/<ch>[/...] -> direct watch URL."""
    m = re.match(r"^/counterstrike/Special:Stream/([a-z]+)/(.+)$", href)
    if not m:
        return None
    kind, rest = m.groups()
    parts = [p for p in rest.split("/") if p]
    if kind == "twitch" and parts:
        return "https://www.twitch.tv/" + parts[0]
    if kind == "youtube" and len(parts) >= 2:
        return "https://www.youtube.com/watch?v=" + parts[-1]
    return None


def _opponent(side_div):
    team = {"name": "", "full": "", "logo": "", "page": ""}
    if side_div is None:
        return team
    img = side_div.find("img")
    if img is not None:
        team["logo"] = img.attrs.get("src", "")
    name_span = side_div.find(cls="name")
    if name_span is not None:
        link = name_span.find("a")
        team["name"] = name_span.text() or "?"
        if link is not None and link.attrs.get("href", "").startswith("/counterstrike/"):
            team["page"] = ROOT + link.attrs["href"]
            team["full"] = link.attrs.get("title", "")
    return team


def _parse_block(block_html):
    root = parse_html(block_html)

    timer = root.find(cls="timer-object")
    if timer is None or not timer.attrs.get("data-timestamp"):
        return None
    match = {
        "start_ts": int(timer.attrs["data-timestamp"]),
        "finished": timer.attrs.get("data-finished") == "finished",
        "team1": _opponent(root.find(cls="match-info-header-opponent-left")),
        "team2": _opponent(_other_opponent(root)),
        "score1": None, "score2": None, "winner": 0, "bestof": "",
        "tournament": {"name": "", "icon": "", "page": ""},
        "streams": [],
    }

    upper = root.find(cls="match-info-header-scoreholder-upper")
    if upper is not None:
        nums = re.findall(r"\d+", upper.text())
        if len(nums) >= 2:
            match["score1"], match["score2"] = int(nums[0]), int(nums[1])
    lower = root.find(cls="match-info-header-scoreholder-lower")
    if lower is not None:
        m = re.search(r"Bo\s*(\d+)", lower.text())
        if m:
            match["bestof"] = "Bo" + m.group(1)

    for side, cls in ((1, "match-info-header-opponent-left"), (2, None)):
        div = root.find(cls=cls) if cls else _other_opponent(root)
        if div is not None and div.has_class("match-info-header-winner"):
            match["winner"] = side

    tname = root.find(cls="match-info-tournament-name")
    if tname is not None:
        match["tournament"]["name"] = tname.text()
        link = tname.find("a")
        if link is not None and link.attrs.get("href", "").startswith("/counterstrike/"):
            match["tournament"]["page"] = ROOT + link.attrs["href"].split("#")[0]
    icons = root.find_all(cls="league-icon-small-image")
    if icons:
        img = icons[-1].find("img")  # darkmode variant is rendered last
        if img is not None:
            match["tournament"]["icon"] = img.attrs.get("src", "")

    seen = set()
    for a in root.find_all("a"):
        url = _stream_url(a.attrs.get("href", ""))
        if url and url not in seen:
            seen.add(url)
            match["streams"].append(url)
    match["streams"] = match["streams"][:2]

    # a running score on an unfinished match means it is being played;
    # a start time long past with no score yet means it just began
    now = time.time()
    if not match["finished"]:
        if match["score1"] is not None:
            match["live"] = True
        elif match["start_ts"] <= now:
            match["live"] = True
        else:
            match["live"] = False
    else:
        match["live"] = False
        if not match["winner"] and match["score1"] is not None:
            if match["score1"] > match["score2"]:
                match["winner"] = 1
            elif match["score2"] > match["score1"]:
                match["winner"] = 2
    return match


def _other_opponent(root):
    """The right-hand opponent div: has the base class but not -left."""
    for div in root.find_all(cls="match-info-header-opponent"):
        if not div.has_class("match-info-header-opponent-left"):
            return div
    return None


def parse_matches(html):
    matches = []
    for block in _match_blocks(html):
        m = _parse_block(block)
        if m is not None and (m["team1"]["name"] or m["team2"]["name"]):
            matches.append(m)
    live = [m for m in matches if m["live"]]
    upcoming = sorted((m for m in matches if not m["live"] and not m["finished"]),
                      key=lambda m: m["start_ts"])
    recent = sorted((m for m in matches if m["finished"]),
                    key=lambda m: m["start_ts"], reverse=True)
    return {"live": live, "upcoming": upcoming, "recent": recent}


# ---------------------------------------------------------------- fetch --
_cache_lock = threading.Lock()
_cache = {"fetched_at": 0.0, "live": [], "upcoming": [], "recent": []}


def fetch(url=API_URL, timeout=FETCH_TIMEOUT):
    """Single gzip-encoded GET; returns decoded bytes or raises."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


def refresh():
    """Fetch, parse and store the ticker. Returns the new state."""
    global _cache
    payload = json.loads(fetch())
    state = parse_matches(payload["parse"]["text"]["*"])
    state["fetched_at"] = time.time()
    with _cache_lock:
        _cache = state
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass
    return state


def _load_disk_cache():
    global _cache
    try:
        state = json.load(open(CACHE_FILE))
        if {"fetched_at", "live", "upcoming", "recent"} <= state.keys():
            with _cache_lock:
                _cache = state
            return state
    except (OSError, ValueError):
        pass
    return None


def matches(stale_ok=True):
    """Current match state for the API layer.

    A fresh cache is returned as-is. A stale cache triggers a background
    refresh and is served meanwhile, so a kiosk poll never blocks on the
    network. Only a service start with no usable cache fetches inline.
    """
    with _cache_lock:
        state = _cache
    if time.time() - state["fetched_at"] < CACHE_TTL:
        return state
    if state["fetched_at"] > 0 and stale_ok:
        threading.Thread(target=_refresh_quietly, daemon=True).start()
        return state
    disk = _load_disk_cache()
    if disk is not None:
        if time.time() - disk["fetched_at"] < CACHE_TTL:
            return disk
        threading.Thread(target=_refresh_quietly, daemon=True).start()
        return disk
    try:
        return refresh()
    except Exception:
        return {"fetched_at": 0.0, "live": [], "upcoming": [], "recent": []}


def _refresh_quietly():
    try:
        refresh()
    except Exception:
        pass  # keep serving the stale cache; the next poll retries


def last_fetch_age():
    with _cache_lock:
        fetched = _cache["fetched_at"]
    return time.time() - fetched if fetched else None
