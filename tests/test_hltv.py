import os

import hltv

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "hltv_match.html")


def fixture():
    with open(FIXTURE) as f:
        return f.read()


def test_parse_veto_kinds_and_teams():
    veto = hltv.parse_veto(fixture())
    assert veto[0] == {"line": "1. Legacy removed Anubis", "kind": "remove", "team": "Legacy"}
    picks = [v for v in veto if v["kind"] == "pick"]
    assert picks == [
        {"line": "3. Legacy picked Mirage", "kind": "pick", "team": "Legacy"},
        {"line": "4. Falcons picked Dust2", "kind": "pick", "team": "Falcons"},
    ]
    assert veto[-1]["kind"] == "left"
    assert len(veto) == 7


def test_parse_maps_scores_and_picks():
    maps = hltv.parse_maps(fixture())
    assert [m["map"] for m in maps] == ["Mirage", "Dust2", "Ancient"]
    m1 = maps[0]
    assert (m1["left"]["score"], m1["right"]["score"]) == ("13", "7")
    assert m1["left"]["won"] is True and m1["right"]["won"] is False
    assert m1["left"]["pick"] is True          # Legacy picked Mirage
    assert m1["halves"] == "5:7;8:0"
    assert m1["finished"] is True
    assert maps[1]["right"]["pick"] is True    # Falcons picked Dust2
    assert maps[1]["finished"] is False
    assert maps[2]["finished"] is False


def test_parse_maps_ignores_name_only_preview_holders():
    """HLTV headers repeat the current map without result sides."""
    html = ('<div class="mapholder"><div class="played"><div class="map-name-holder">'
            '<div class="mapname">Mirage</div></div></div></div>'
            '<div class="mapholder"><div class="played"><div class="mapname">Dust2</div></div>'
            '<div class="results played">'
            '<div class="results-left won pick"><div class="results-team-score">13</div></div>'
            '<span class="results-right lost "><div class="results-team-score">9</div></span>'
            '</div></div>')
    maps = hltv.parse_maps(html)
    assert [m["map"] for m in maps] == ["Dust2"]


def test_parse_round_score_shapes():
    assert hltv.parse_round_score(
        '<div class="match-header-vs-score ">'
        '<span class="match-header-vs-score-first">7</span>:'
        '<span class="match-header-vs-score-second">5</span></div>') == [7, 5]
    assert hltv.parse_round_score('{"currentScore": "12-9"}') == [12, 9]
    assert hltv.parse_round_score("<html>no score here</html>") is None


def test_find_live_match_links_and_matching():
    html = ('<a href="/matches/2396609/legacy-vs-falcons-esports-world-cup-2026" '
            'class="match-top a-reset">x</a>'
            '<a href="/matches/2396610/sparta-vs-l-g-21-08-2026" '
            'class="match-top a-reset">y</a>')
    links = hltv.find_live_match_links(html)
    assert links[0]["id"] == "2396609"
    assert links[0]["teams"] == ["Legacy", "Falcons"]
    lp = {"team1": {"name": "Legacy"}, "team2": {"name": "Falcons"}}
    assert hltv.match_live_link(lp, links)["id"] == "2396609"
    lp2 = {"team1": {"name": "Nobody"}, "team2": {"name": "Matches"}}
    assert hltv.match_live_link(lp2, links) is None


def test_read_state_degrades():
    state = hltv.read_state()
    assert state["maps"] == [] and state["round"] is None


def test_read_state_roundtrip(tmp_path, monkeypatch):
    import json
    f = tmp_path / "hltv_detail.json"
    monkeypatch.setattr(hltv, "STATE_FILE", str(f))
    data = {"updated": 123.0, "url": "u", "veto": [], "maps": [{"map": "Mirage"}],
            "round": [7, 5], "teams": ["Legacy", "Falcons"]}
    f.write_text(json.dumps(data))
    assert hltv.read_state()["round"] == [7, 5]
