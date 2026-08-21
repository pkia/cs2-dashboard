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
    return `<img src="/api/logo?path=${encodeURIComponent(path)}" alt="" ` +
           `onerror="this.parentNode.style.visibility='hidden'">`;
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
            `<div class="team-full">${esc(t1.full || "")}</div></div></div>` +
        `<div class="score-cell">${score}${note}</div>` +
        `<div class="match-side right${lost2 ? " lost" : ""}">` +
            `<span class="team-logo">${logoImg(t2.logo)}</span>` +
            `<div class="team-cell"><div class="team-short">${esc(t2.name || "TBD")}</div>` +
            `<div class="team-full">${esc(t2.full || "")}</div></div></div>` +
        `</div></div>`;
}

function render() {
    const now = Date.now();

    // live
    $("live-list").innerHTML = state.live.map((m) => matchCard(m, now)).join("");
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

$("maritime-btn").addEventListener("click", () => {
    location.href = location.protocol + "//" + location.hostname + ":8000/";
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
setInterval(load, REFRESH_MS);
setInterval(tickCountdowns, TICK_MS);
setInterval(clock, 1000);
