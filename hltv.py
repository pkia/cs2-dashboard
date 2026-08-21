"""HLTV match-detail layer: veto, map results, live round score.

bo3.gg gives series score and map statuses; Liquipedia gives the
schedule. Neither publishes the map veto or the running round score.
HLTV's match pages have all of it, but sit behind bot protection, so
this data is collected by scripts/hltv_detail.py (a camoufox feeder
service) and handed over via hltv_detail.json. This module holds the
pure parts - parsing and matching - so they are testable without a
browser, and the web app never imports camoufox.
"""
import json
import os
import re
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "hltv_detail.json")


def parse_veto(html):
    """The veto lines: '1. Legacy removed Anubis', '3. ... picked Mirage'."""
    out = []
    for raw in re.findall(r"<div>\s*(\d+\.\s*[^<]+?)\s*</div>", html):
        line = re.sub(r"\s+", " ", raw).strip()
        kind = ("pick" if re.search(r"\bpicked\b", line, re.I)
                else "remove" if re.search(r"\bremoved\b|\bbanned\b", line, re.I)
                else "left" if re.search(r"\bleft over\b|\bremains\b|\bdecider\b", line, re.I)
                else "note")
        team = ""
        m2 = re.match(r"\d+\.\s+(.+?)\s+(?:picked|removed|banned)\b", line, re.I)
        if m2:
            team = m2.group(1)
        m3 = re.match(r"\d+\.\s+(.+?)\s+was left over", line, re.I)
        if m3:
            team = m3.group(1)
        out.append({"line": line, "kind": kind, "team": team})
    return out


def parse_maps(html):
    """Per-map rows: name, score, winner side, pick owner, half scores."""
    maps = []
    for row in re.findall(r'<div class="mapholder">(.*?)(?=<div class="mapholder">|$)',
                          html, re.S):
        name = re.search(r'<div class="mapname">([^<]+)</div>', row)
        if not name:
            continue
        scores = re.findall(r'<div class="results-team-score">([^<]*)</div>', row)
        # header "current map" previews carry a name but no result sides
        if not re.search(r'class="results-(left|right)', row):
            continue
        cls = {}
        for which in ("left", "right"):
            m = re.search(r'class="results-%s\s+([^"]*)"' % which, row)
            cls[which] = m.group(1) if m else ""

        def side(which, score):
            return {
                "score": score,
                "won": "won" in cls[which],
                "pick": "pick" in cls[which],
            }

        halves = re.search(r'<div class="results-center-half-score">(.*?)</div>', row, re.S)
        half_txt = re.sub(r"<[^>]+>", "", halves.group(1)) if halves else ""
        half_txt = re.sub(r"\s+", "", half_txt).strip("()")
        entry = {
            "map": name.group(1).strip(),
            "left": side("left", scores[0].strip() if scores else None),
            "right": side("right", scores[-1].strip() if len(scores) > 1 else None),
            "halves": half_txt,
        }
        entry["finished"] = entry["left"]["score"] not in (None, "") and \
            entry["right"]["score"] not in (None, "", "-")
        # HLTV repeats the current map in the header as a scoreless
        # duplicate - keep the row that actually carries scores
        prev = next((e for e in maps if e["map"] == entry["map"]), None)
        if prev is None:
            maps.append(entry)
        elif entry["finished"] or (not prev["finished"] and entry["left"]["score"]):
            maps[maps.index(prev)] = entry
    return maps


def _strip_tags(fragment):
    txt = re.sub(r"<!--.*?-->", "", fragment, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt)).strip()


def parse_scorebot(html):
    """The live scorebot widget: running round score, round number, map,
    round timer, and which team is on which side. None when no map is on.

    Markup (2026 HLTV match page):
      <div class="score scoreText"><div class="ctScore">1</div>...
      <span class="currentRoundText">R: 4 - Dust2</span>
      <div class="timeText"><span>1:47</span></div>
    """
    if "scoreText" not in html:
        return None
    ct = re.search(r'class="ctScore">\s*(\d+)\s*<', html)
    t = re.search(r'class="tScore">\s*(\d+)\s*<', html)
    if not (ct and t):
        return None
    out = {
        "round": [int(ct.group(1)), int(t.group(1))],   # [CT side, T side]
        "ct_team": "", "t_team": "",
        "round_num": None, "map": "", "timer": "",
    }
    rt = re.search(r'class="currentRoundText">(.*?)</span>', html, re.S)
    if rt:
        m2 = re.search(r"^\s*(\d+)\s*-\s*(.+)$", _strip_tags(rt.group(1)))
        if m2:
            out["round_num"] = int(m2.group(1))
            out["map"] = m2.group(2).strip()
    timer = re.search(r'class="timeText">\s*<span>\s*([\d:]+)\s*</span>', html)
    if timer:
        out["timer"] = timer.group(1)
    # the two team tables: the CT one carries ctTeamHeaderBg in its thead
    for tbl in re.findall(r'<table class="team">.*?</table>', html, re.S):
        nm = re.search(r'<div class="teamName">(.*?)</div>', tbl, re.S)
        if not nm:
            continue
        name = _strip_tags(nm.group(1))
        if not name:
            continue
        if "ctTeamHeaderBg" in tbl:
            out["ct_team"] = name
        else:
            out["t_team"] = name
    return out


def parse_round_score(html):
    """Compatibility shim: just the running score, team-ordered later."""
    sb = parse_scorebot(html)
    return sb["round"] if sb else None


def find_live_match_links(matches_html):
    """Live match page links + team names from HLTV's /matches page."""
    out = []
    for m in re.finditer(
            r'<a[^>]+href="(/matches/(\d+)/([^"]+))"[^>]*class="[^"]*match-top[^"]*".*?</a>',
            matches_html, re.S):
        url, mid, slug = m.group(1), m.group(2), m.group(3)
        # slug: <team1>-vs-<team2>-<event...>; team2 ends at the next dash
        parts = slug.split("-vs-")
        t1 = parts[0] if parts else ""
        t2 = parts[1].split("-")[0] if len(parts) > 1 else ""
        teams = [t.replace("-", " ").title() for t in (t1, t2) if t]
        out.append({"url": url, "id": mid, "teams": teams[:2]})
    return out


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def match_live_link(live_match, links):
    """Pick the HLTV live match whose teams overlap a Liquipedia match."""
    lp = {_norm(live_match["team1"]["name"]), _norm(live_match["team2"]["name"])}
    lp.discard("")
    for l in links:
        cand = {_norm(t) for t in l["teams"]}
        cand.discard("")
        if cand & lp:
            return l
    return None


def read_state():
    try:
        state = json.load(open(STATE_FILE))
        if {"updated", "url", "maps"} <= state.keys():
            return state
    except (OSError, ValueError):
        pass
    return {"updated": 0.0, "url": "", "veto": [], "maps": [], "round": None,
            "teams": []}


def state_age(state=None):
    state = state or read_state()
    return time.time() - state["updated"] if state["updated"] else None
