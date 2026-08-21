/* CS2 dashboard kiosk front-end: renders /api/matches, keeps countdowns
 * ticking, auto-refreshes once a minute, and rotates tabs when enabled. */
"use strict";

const REFRESH_MS = 60000;
const TICK_MS = 10000;
const ROTATE_MS = 20000;

let state = { live: [], upcoming: [], recent: [], fetched_at: 0, ok: false };
let rotate = false;
let rotateTimer = null;
let lastInteraction = Date.now();

/* ---- helpers ---- */
const $ = (id) => document.getElementById(id);

function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
}

function logoImg(path) {
    if (!path) return "";
    const q = path.startsWith("http")
        ? "url=" + encodeURIComponent(path)     // bo3.gg full URL
        : "path=" + encodeURIComponent(path);   // liquipedia path
    return `<img src="/api/logo?${q}" alt="" ` +
           `onerror="this.parentNode.style.visibility='hidden'">`;
}

/* bo3.gg live detail helpers ------------------------------------------ */

function roundScore(detail) {
    /* Snapshot schema is undocumented; look for the common shapes a
     * round-score payload would take and render it if we find one. */
    const snap = detail && detail.snapshot;
    if (!snap) return null;
    const s = snap.results || snap.data || snap;
    const pairs = [
        [s.score_team1, s.score_team2], [s.team1_score, s.team2_score],
        [s.t1_score, s.t2_score], [s.rounds_team1, s.rounds_team2],
    ];
    for (const [a, b] of pairs) {
        if (Number.isInteger(a) && Number.isInteger(b)) return [a, b];
    }
    return null;
}

function mapStrip(detail) {
    const games = (detail && detail.games) || [];
    if (!games.length) return "";
    return `<div class="map-strip">` + games.map((g) => {
        const cls = g.status === "current" ? " current"
            : g.status === "finished" ? " done" : "";
        const label = g.map ? `${g.number} · ${g.map}` : `Map ${g.number}`;
        const live = g.status === "current" ? `<span class="live-dot"></span>` : "";
        return `<span class="map-pill${cls}">${live}${esc(label)}</span>`;
    }).join("") + `</div>`;
}

function rankChip(rank) {
    return rank ? `<span class="rank-chip">#${rank}</span>` : "";
}

let streamOn = localStorage.getItem("stream-on") !== "off";
let streamPick = null;  // name of the manually chosen stream, if any

function streamEmbed(m) {
    const d = m.detail;
    if (!d || !d.streams || !d.streams.length) return "";
    const streams = d.streams;
    let s = streams.find((x) => x.name === streamPick)
        || streams.find((x) => x.lang === "en")
        || streams[0];
    let src = s.embed || "";
    if (!src) return "";
    if (src.includes("twitch")) {
        src += (src.includes("?") ? "&" : "?") +
            `muted=true&autoplay=true&parent=${location.hostname}`;
    } else if (src.includes("youtube")) {
        src += (src.includes("?") ? "&" : "?") + "autoplay=1&mute=1";
    }
    const viewers = (n) => n ? `${n.toLocaleString()} watching` : "";
    const chips = streams.map((x) => {
        const sel = x.name === s.name ? " sel" : "";
        const v = x.viewers ? ` · ${x.viewers.toLocaleString()}` : "";
        return `<button class="stream-chip-live${sel}" data-stream="${esc(x.name)}">` +
            `${esc(x.name)}${v}</button>`;
    }).join("");
    const body = streamOn
        ? `<iframe class="stream-frame" src="${esc(src)}" allowfullscreen
             sandbox="allow-scripts allow-same-origin allow-presentation"
             referrerpolicy="no-referrer" loading="lazy"></iframe>`
        : "";
    return `<div class="stream-box">
        <div class="stream-bar">
            <span>📺 ${esc(s.name || "stream")} ${esc(viewers(s.viewers) ? "· " + viewers(s.viewers) : "")}</span>
            <button class="stream-toggle">${streamOn ? "⏹ stop" : "▶ watch"}</button>
        </div>
        <div class="stream-chips">${chips}</div>${body}</div>`;
}

function featuredLive(m) {
    const t1 = m.team1 || {}, t2 = m.team2 || {};
    const d = m.detail || {};
    const b1 = (d.teams && d.teams[0]) || {}, b2 = (d.teams && d.teams[1]) || {};
    const series = (d.series && Number.isInteger(d.series[0])) ? d.series
        : [m.score1, m.score2];
    const rs = roundScore(d);
    const score = Number.isInteger(series[0])
        ? `<div class="score">${series[0]}<span class="sep">:</span>${series[1]}</div>`
        : `<div class="score vs">vs</div>`;
    const roundLine = rs
        ? `<div class="round-score">round ${rs[0]} – ${rs[1]}</div>` : "";
    const bo = m.bestof || (d.bestof ? "Bo" + d.bestof : "");
    return `<div class="featured">
        <div class="match-top">
            <span class="live-pill big"><span class="live-dot"></span>LIVE</span>
            <span class="tournament-name">${esc((m.tournament || {}).name || "")}</span>
            <span class="top-right">${esc(bo)}</span>
        </div>
        <div class="feat-row">
            <div class="feat-team">
                <span class="team-logo big">${logoImg(t1.logo || b1.logo)}</span>
                <div class="team-short">${esc(t1.name || b1.name || "TBD")}</div>
                <div class="team-full">${esc(b1.full || "")} ${rankChip(b1.rank)}</div>
            </div>
            <div class="score-cell big">${score}${roundLine}</div>
            <div class="feat-team right">
                <span class="team-logo big">${logoImg(t2.logo || b2.logo)}</span>
                <div class="team-short">${esc(t2.name || b2.name || "TBD")}</div>
                <div class="team-full">${rankChip(b2.rank)} ${esc(b2.full || "")}</div>
            </div>
        </div>
        ${mapStrip(d)}
        ${streamEmbed(m)}
    </div>`;
}

function countdown(ts, now) {
    const d = ts * 1000 - now;
    if (d <= -3600000) return "in progress";
    if (d <= 0) return "starting now";
    const mins = Math.round(d / 60000);
    if (mins < 60) return `in ${mins}m`;
    const h = Math.floor(mins / 60);
    if (h < 24) return `in ${h}h ${mins % 60}m`;
    const dt = new Date(ts * 1000);
    const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const t = `${String(dt.getHours()).padStart(2, "0")}:${String(dt.getMinutes()).padStart(2, "0")}`;
    if (h < 168) return `${days[dt.getDay()]} ${t}`;
    return `${dt.getDate()}/${dt.getMonth() + 1} ${t}`;
}

function timeHM(ts) {
    const dt = new Date(ts * 1000);
    return `${String(dt.getHours()).padStart(2, "0")}:${String(dt.getMinutes()).padStart(2, "0")}`;
}

function dayKey(ts) {
    const dt = new Date(ts * 1000);
    const today = new Date();
    const tomorrow = new Date(today.getTime() + 86400000);
    if (dt.toDateString() === today.toDateString()) return "Today";
    if (dt.toDateString() === tomorrow.toDateString()) return "Tomorrow";
    return dt.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
}

function streamChips(streams) {
    return streams.map((u) => {
        const m = u.match(/twitch\.tv\/(.+)/);
        const label = m ? `📺 ${m[1]}` : "▶ YouTube";
        return `<span class="stream-chip">${esc(label)}</span>`;
    }).join("");
}

/* ---- rendering ---- */
function matchCard(m, now, opts = {}) {
    const t1 = m.team1 || {}, t2 = m.team2 || {};
    const lost1 = opts.finished && m.winner === 2;
    const lost2 = opts.finished && m.winner === 1;

    let score, note;
    if (m.score1 !== null && m.score1 !== undefined) {
        score = `<div class="score">` +
            `<span class="s ${m.winner === 1 ? "win" : ""}">${m.score1}</span>` +
            `<span class="sep">:</span>` +
            `<span class="s ${m.winner === 2 ? "win" : ""}">${m.score2}</span></div>`;
    } else {
        score = `<div class="score vs">vs</div>`;
    }

    if (m.live) {
        const d = m.detail || {};
        const series = (d.series && Number.isInteger(d.series[0])) ? d.series
            : [m.score1, m.score2];
        if (Number.isInteger(series[0])) {
            score = `<div class="score">` +
                `<span class="s">${series[0]}</span>` +
                `<span class="sep">:</span>` +
                `<span class="s">${series[1]}</span></div>`;
        }
        note = `<span class="live-pill"><span class="live-dot"></span>LIVE</span>` +
               `<div class="score-note">${esc(m.bestof || "")}</div>`;
    } else if (m.finished) {
        note = `<div class="final-tag">FINAL</div>` +
               `<div class="score-note">${esc(m.bestof || "")}</div>`;
    } else {
        note = `<div class="countdown" data-ts="${m.start_ts}">${countdown(m.start_ts, now)}</div>` +
               `<div class="score-note">${esc(m.bestof || "")} · ${timeHM(m.start_ts)}</div>`;
    }

    const icon = m.tournament && m.tournament.icon
        ? `<img class="ticon" src="/api/logo?path=${encodeURIComponent(m.tournament.icon)}" alt="" onerror="this.remove()">`
        : "";
    const tname = m.tournament && m.tournament.name ? m.tournament.name : "Tournament TBD";
    const right = m.live
        ? streamChips(m.streams)
        : (opts.finished ? "" : `<span class="countdown" data-ts="${m.start_ts}">${countdown(m.start_ts, now)}</span>`);

    return `<div class="match${m.live ? " is-live" : ""}">` +
        `<div class="match-top">${icon}<span class="tournament-name">${esc(tname)}</span>` +
        `<span class="top-right">${right}</span></div>` +
        `<div class="match-row">` +
        `<div class="match-side${lost1 ? " lost" : ""}">` +
            `<span class="team-logo">${logoImg(t1.logo)}</span>` +
            `<div class="team-cell"><div class="team-short">${esc(t1.name || "TBD")}</div>` +
            `<div class="team-full">${esc(t1.full || "")} ${rankChip((m.detail || {}).teams && m.detail.teams[0] && m.detail.teams[0].rank)}</div></div></div>` +
        `<div class="score-cell">${score}${note}</div>` +
        `<div class="match-side right${lost2 ? " lost" : ""}">` +
            `<span class="team-logo">${logoImg(t2.logo)}</span>` +
            `<div class="team-cell"><div class="team-short">${esc(t2.name || "TBD")}</div>` +
            `<div class="team-full">${rankChip((m.detail || {}).teams && m.detail.teams[1] && m.detail.teams[1].rank)} ${esc(t2.full || "")}</div></div></div>` +
        `</div>${m.live ? mapStrip(m.detail) : ""}</div>`;
}

function render() {
    const now = Date.now();

    // live: featured match (prefer one with bo3 detail) + the rest as cards
    const withDetail = state.live.filter((m) => m.detail);
    const featured = withDetail.length ? withDetail[0] : state.live[0];
    const rest = state.live.filter((m) => m !== featured);
    $("featured-live").innerHTML = featured ? featuredLive(featured) : "";
    $("live-list").innerHTML = rest.map((m) => matchCard(m, now)).join("");
    bindStreamControls();
    $("live-count").textContent = state.live.length;
    $("live-count").classList.toggle("hidden", state.live.length === 0);
    $("live-empty").classList.toggle("hidden", state.live.length > 0);
    if (state.live.length === 0) {
        const next = state.upcoming[0];
        $("live-next").textContent = next
            ? `Next: ${next.team1.name} vs ${next.team2.name} — ${countdown(next.start_ts, now)}`
            : "";
    }

    // upcoming, grouped by day
    const groups = [];
    for (const m of state.upcoming) {
        const k = dayKey(m.start_ts);
        if (!groups.length || groups[groups.length - 1].key !== k) groups.push({ key: k, items: [] });
        groups[groups.length - 1].items.push(m);
    }
    $("upcoming-list").innerHTML = groups.map((g) =>
        `<div class="day-header">${esc(g.key)}</div>` +
        g.items.map((m) => matchCard(m, now)).join("")).join("");
    $("upcoming-empty").classList.toggle("hidden", state.upcoming.length > 0);

    // results
    $("results-list").innerHTML = state.recent.map((m) => matchCard(m, now, { finished: true })).join("");
    $("results-empty").classList.toggle("hidden", state.recent.length > 0);

    // freshness in the bottom bar
    const age = state.fetched_at ? Math.round((now / 1000) - state.fetched_at) : null;
    const right = $("clock-right");
    if (age === null) {
        right.textContent = "waiting for first fetch…";
        right.className = "stale";
    } else if (age > 600) {
        right.textContent = `Liquipedia unreachable — showing ${Math.round(age / 60)} min old data`;
        right.className = "stale";
    } else {
        right.textContent = `updated ${age < 60 ? age + "s" : Math.round(age / 60) + " min"} ago`;
        right.className = "";
    }
}

/* recompute countdowns without a full re-render */
function tickCountdowns() {
    const now = Date.now();
    document.querySelectorAll(".countdown[data-ts]").forEach((el) => {
        el.textContent = countdown(parseInt(el.dataset.ts, 10), now);
    });
    if (state.live.length === 0 && state.upcoming[0]) {
        const next = state.upcoming[0];
        $("live-next").textContent =
            `Next: ${next.team1.name} vs ${next.team2.name} — ${countdown(next.start_ts, now)}`;
    }
}

/* ---- data ---- */
async function load() {
    try {
        const r = await fetch("/api/matches");
        const j = await r.json();
        if (j && Array.isArray(j.live)) {
            state = j;
            render();
        }
    } catch (e) {
        // keep whatever is on screen; the freshness note flags staleness
    }
}

function bindStreamControls() {
    const toggle = document.querySelector(".stream-toggle");
    if (toggle && !toggle.dataset.bound) {
        toggle.dataset.bound = "1";
        toggle.addEventListener("click", () => {
            streamOn = !streamOn;
            localStorage.setItem("stream-on", streamOn ? "on" : "off");
            render();
        });
    }
    document.querySelectorAll(".stream-chip-live").forEach((chip) => {
        if (chip.dataset.bound) return;
        chip.dataset.bound = "1";
        chip.addEventListener("click", () => {
            streamPick = chip.dataset.stream;
            render();
        });
    });
}

/* ---- tabs & rotation ---- */
function showTab(name) {
    document.querySelectorAll(".tab").forEach((b) =>
        b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab-content").forEach((c) =>
        c.classList.toggle("active", c.id === "tab-" + name));
}

function nextTab() {
    const order = ["live", "upcoming", "results"];
    const cur = document.querySelector(".tab.active");
    const i = order.indexOf(cur ? cur.dataset.tab : "live");
    showTab(order[(i + 1) % order.length]);
}

function setRotate(on) {
    rotate = on;
    $("auto-rotate").classList.toggle("on", on);
    $("auto-rotate").textContent = on ? "⏸" : "▶";
    if (rotateTimer) clearInterval(rotateTimer);
    if (on) rotateTimer = setInterval(() => {
        if (Date.now() - lastInteraction > 15000) nextTab();
    }, ROTATE_MS);
}

/* ---- boot ---- */
document.querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => {
        lastInteraction = Date.now();
        showTab(b.dataset.tab);
    }));

// ?tab=live|upcoming|results deep link (used for testing and linking)
{
    const wanted = new URLSearchParams(location.search).get("tab");
    if (wanted && document.getElementById("tab-" + wanted)) showTab(wanted);
}

$("home-btn").addEventListener("click", () => {
    // Back to the kiosk chooser (kiosk-home on :8091)
    location.href = location.protocol + "//" + location.hostname + ":8091/";
});
$("auto-rotate").addEventListener("click", () => {
    lastInteraction = Date.now();
    setRotate(!rotate);
});

function clock() {
    const d = new Date();
    $("clock-time").textContent =
        `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

document.addEventListener("touchstart", () => { lastInteraction = Date.now(); }, { passive: true });

load();
clock();
// adaptive poll: 30s while matches are live, 60s otherwise
(function pollLoop() {
    load();
    setTimeout(pollLoop, state.live.length ? 30000 : REFRESH_MS);
})();
setInterval(tickCountdowns, TICK_MS);
setInterval(clock, 1000);
