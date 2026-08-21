# CS2 Dashboard

![CI](https://github.com/pkia/cs2-dashboard/actions/workflows/ci.yml/badge.svg)

A pro Counter-Strike 2 match dashboard for the Raspberry Pi touchscreen
kiosk — the esports counterpart to the maritime dashboard next door on
`http://<host>:8000`.

## What it shows

- **Live tab** — matches being played right now with the running series
  score, a pulsing LIVE marker, format (Bo3/…) and stream channels
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
`/api/logo` endpoint (allow-listed to `/commons/images/` on
liquipedia.net) and cached on disk under `static/logos/`.

## How it runs

Same pattern as the other services on the host: Flask on port 8001
(`cs2-dashboard.service`), pull-based CD via the deploy timer, CI on
every push (ruff fatal rules, byte-compile, pytest). The UI targets the
1024×600 kiosk touchscreen: big touch targets, auto-refresh every
minute, optional tab auto-rotate, and a ⛵ Marine button that flips the
kiosk to the maritime dashboard (which has a matching button back).

## Local development

```bash
venv/bin/python app.py        # dashboard on :8001
venv/bin/python -m pytest -v
```

Tests run fully offline against a trimmed HTML fixture captured from a
real ticker page (plus a synthetic live match, since none were running
at capture time).
