# CS2 Dashboard

![CI](https://github.com/pkia/cs2-dashboard/actions/workflows/ci.yml/badge.svg)

A pro Counter-Strike 2 match dashboard for the Raspberry Pi touchscreen
kiosk. One of the dashboards reachable from the kiosk chooser
(kiosk-home on `http://<host>:8091`).

## What it shows

- **Live tab** — the featured match as a stream-dominant card: the game
  video (muted autoplay, tap ⏹ to stop, channel switcher) with the map
  pills and veto line beneath it, and beside it the series score, the
  near-live running round score (`11 – 10 · R22`), team world ranks and
  an "Up next" panel with the following matches
- **Upcoming tab** — the next week of pro play grouped by day, with
  per-match countdowns ("in 2h 15m")
- **Results tab** — recently completed series with final scores, winners
  highlighted and losers dimmed

Every card shows both teams (logo, short tag, full name), the tournament
name and its icon — e.g. "Esports World Cup 2026 - Playoffs".

## Data source

[Liquipedia](https://liquipedia.net/counterstrike) — the community wiki
every CS esports follower knows. One request to their public parse API
(`Liquipedia:Matches`) returns live, upcoming and completed matches in a
single page, which is parsed into structured match dicts by
`liquipedia.py` (a small DOM-tree parser, no heavy dependencies).

Rate-limit friendly by design, per their
[API terms](https://liquipedia.net/api-terms-of-use):

- one fetch per refresh, refresh at most every 2 minutes (limit is 1/2s)
- descriptive User-Agent, gzip-encoded requests
- a stale cache is served instantly and refreshed in a background
  thread, so a slow or down Liquipedia never blocks the kiosk — the
  footer just flags how old the data is

Team logos and tournament icons are fetched once through the
`/api/logo` endpoint (allow-listed to Liquipedia's and bo3.gg's image
hosts) and cached on disk under `static/logos/`.

## Live in-match detail (bo3.gg)

Liquipedia only publishes a series score between maps, so for what is
happening *inside* a match — current map, round scores when available,
official per-match streams, world ranks — `bo3live.py` talks to
[bo3.gg](https://bo3.gg)'s public API and push socket:

- `api.bo3.gg/api/v2/matches/live` — which matches are on, series
  score, current map
- `api.bo3.gg/api/v1/matches/<slug>?with=…` — teams, ranks, streams
- `api.bo3.gg/api/v1/live/matches/<id>/last_snapshot` — round-level
  snapshot, present whenever their coverage is producing one
- `wss://updates.bo3.gg/ws` — push events used **only** as a
  "something changed" trigger to re-poll REST, so event payload
  changes can never break the dashboard

bo3 detail is attached to Liquipedia matches by team name (with a
single-live-match fallback). If any of it fails — API blocked, socket
down, no coverage for a match — the dashboard simply shows what
Liquipedia has. The embedded player runs muted per kiosk-browser
autoplay policy; the toggle choice is remembered.

## Map veto and live round scores (HLTV)

Neither Liquipedia nor bo3.gg publishes the map veto or the running
round score; HLTV has both but sits behind bot protection. The
`cs2-hltv` feeder service (`scripts/hltv_detail.py`) uses
[camoufox](https://github.com/daijro/camoufox) (the stealth Firefox
already on this host) to render the live match page and extract the
veto sequence, per-map results with half scores and the live round
score. For near-live scores it keeps that page open — the page itself
maintains HLTV's scorebot websocket — and reads the numbers out of the
live DOM every 8 s; full page renders happen only when the tracked
match changes or every 5 minutes. The browser closes when nothing is
live. Pure parsing lives in `hltv.py` so it is covered by tests
without a browser, and the web app never imports camoufox — if the
feeder dies the dashboard just shows the other sources' data.

## How it runs

Same pattern as the other services on the host: Flask on port 8001
(`cs2-dashboard.service`), pull-based CD via the deploy timer, CI on
every push (ruff fatal rules, byte-compile, pytest), plus the
`cs2-hltv.service` feeder described above. The UI targets the
1024×600 kiosk touchscreen: big touch targets, adaptive auto-refresh
(12 s while a match is live), optional tab auto-rotate, and a ⌂ Home
button that returns to the kiosk chooser.

## Local development

```bash
venv/bin/python app.py        # dashboard on :8001
venv/bin/python -m pytest -v
```

Tests run fully offline against fixtures captured from real ticker and
match pages (plus a synthetic live match, since none were running at
capture time).
