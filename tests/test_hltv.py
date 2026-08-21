import os

import hltv

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "hltv_match.html")


def fixture():
    with open(FIXTURE) as f:
        return f.read()


def test_parse_veto_kinds_teams_and_maps():
    veto = hltv.parse_veto(fixture())
    assert veto[0] == {"line": "1. Legacy removed Anubis", "kind": "remove",
                       "team": "Legacy", "map": "Anubis"}
    picks = [v for v in veto if v["kind"] == "pick"]
    assert [(v["team"], v["map"]) for v in picks] == [("Legacy", "Mirage"),
                                                      ("Falcons", "Dust2")]
    assert veto[-1]["kind"] == "left"
    assert veto[-1]["map"] == "Ancient"
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


def test_parse_round_score_delegates_to_scorebot():
    assert hltv.parse_round_score(SCOREBOT_HTML) == [1, 2]
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


def test_read_state_degrades(tmp_path, monkeypatch):
    # isolate from any real hltv_detail.json the feeder may have written
    monkeypatch.setattr(hltv, "STATE_FILE", str(tmp_path / "missing.json"))
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


SCOREBOT_HTML = '''
<div class="scorebot"><div class="scoreboard">
<div class="score scoreText"><div class="ctScore">1</div>
<div class="scoreSeparater">:</div><div class="tScore">2</div></div>
<span class="roundText">R: <span class="currentRoundText">
<!-- react-text: 294 -->4<!-- /react-text -->
<!-- react-text: 15 --> - <!-- /react-text -->
<!-- react-text: 16 -->Dust2<!-- /react-text -->
</span></span>
<div class="timeText"><span>1:47</span></div>
<table class="team"><thead class="ctTeamHeaderBg"><tr><td>
<div class="teamName"><img src="x.png" alt=""> Legacy</div></td></tr></thead></table>
<table class="team"><thead class="tTeamHeaderBg"><tr><td>
<div class="teamName"><img src="y.png" alt=""> Falcons</div></td></tr></thead></table>
</div></div>
'''


def test_parse_scorebot_full():
    sb = hltv.parse_scorebot(SCOREBOT_HTML)
    assert sb["round"] == [1, 2]
    assert sb["ct_team"] == "Legacy"
    assert sb["t_team"] == "Falcons"
    assert sb["round_num"] == 4
    assert sb["map"] == "Dust2"
    assert sb["timer"] == "1:47"


def test_parse_scorebot_absent_when_no_map():
    assert hltv.parse_scorebot("<html>break time</html>") is None
