import os
import time

import liquipedia

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "matches.html")


def parsed():
    with open(FIXTURE) as f:
        return liquipedia.parse_matches(f.read())


def test_fixture_splits_into_live_upcoming_recent():
    s = parsed()
    assert len(s["live"]) == 1
    assert len(s["upcoming"]) == 2
    assert len(s["recent"]) == 3


def test_live_match_has_running_score_and_no_winner():
    m = parsed()["live"][0]
    assert m["live"] is True
    assert m["finished"] is False
    assert (m["score1"], m["score2"]) == (1, 0)
    assert m["winner"] == 0  # not final while the series is running
    assert m["start_ts"] <= time.time()


def test_upcoming_matches_are_sorted_and_unscored():
    ups = parsed()["upcoming"]
    assert [u["start_ts"] for u in ups] == sorted(u["start_ts"] for u in ups)
    for m in ups:
        assert m["live"] is False
        assert m["score1"] is None


def test_finished_match_fields():
    m = parsed()["recent"][0]
    assert m["finished"] is True
    assert m["team1"]["name"] == "FUT"
    assert m["team2"]["name"] == "MOUZ"
    assert (m["score1"], m["score2"]) == (2, 0)
    assert m["winner"] == 1
    assert m["bestof"] == "Bo3"
    assert m["tournament"]["name"] == "Esports World Cup 2026 - Playoffs"
    assert m["tournament"]["page"].startswith("https://liquipedia.net/counterstrike/")
    assert m["tournament"]["icon"].startswith("/commons/images/")


def test_team_metadata_extracted():
    m = parsed()["recent"][0]
    assert m["team1"]["logo"].startswith("/commons/images/")
    assert m["team1"]["page"].startswith("https://liquipedia.net/counterstrike/")
    assert m["team1"]["full"]  # full name from the link title attribute


def test_winner_falls_back_to_score_when_class_missing():
    html = open(FIXTURE).read().replace("match-info-header-winner", "match-info-header-w")
    recents = liquipedia.parse_matches(html)["recent"]
    assert recents[0]["winner"] == 1


def test_stream_url_conversion():
    u = liquipedia._stream_url("/counterstrike/Special:Stream/twitch/esl_csgo")
    assert u == "https://www.twitch.tv/esl_csgo"
    u = liquipedia._stream_url(
        "/counterstrike/Special:Stream/youtube/Esports_World_Cup/BNNhtj9sAzo")
    assert u == "https://www.youtube.com/watch?v=BNNhtj9sAzo"
    assert liquipedia._stream_url("/counterstrike/FURIA") is None


def test_parse_empty_and_garbage_html():
    assert liquipedia.parse_matches("") == {"live": [], "upcoming": [], "recent": []}
    assert liquipedia.parse_matches("<div>random</div>")["live"] == []


def test_started_match_without_score_counts_as_live():
    """Match kicked off but Liquipedia has not published a score yet."""
    m = liquipedia._parse_block(
        '<div class="match-info"><span class="match-info-countdown">'
        f'<span class="timer-object" data-timestamp="{int(time.time()) - 600}">x</span></span>'
        '<div class="match-info-header">'
        '<div class="match-info-header-opponent match-info-header-opponent-left">'
        '<div class="block-team flipped"><span class="name"><a href="/counterstrike/FURIA">FURIA</a></span></div></div>'
        '<div class="match-info-header-scoreholder">'
        '<span class="match-info-header-scoreholder-scorewrapper">'
        '<span class="match-info-header-scoreholder-upper">vs</span>'
        '<span class="match-info-header-scoreholder-lower">(Bo3)</span>'
        '</span></div>'
        '<div class="match-info-header-opponent">'
        '<div class="block-team"><span class="name"><a href="/counterstrike/FURIA">FURIA</a></span></div></div>'
        "</div></div>")
    assert m["live"] is True
    assert m["score1"] is None
