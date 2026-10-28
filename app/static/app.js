"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v, d = 1) => (v == null ? "—" : (v * 100).toFixed(d) + "%");
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const ago = (iso) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 90) return Math.max(0, Math.round(s)) + " s ago";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 129600) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
};
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");
const dur = (s) => (s == null ? "—" : s < 3600 ? Math.round(s / 60) + " min" : s < 172800 ? (s / 3600).toFixed(1) + " h" : (s / 86400).toFixed(1) + " d");
async function get(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(path + " → " + r.status);
  return r.json();
}

/* ---------- motion & small building blocks (all motion off under prefers-reduced-motion) ---------- */
const skeleton = (cards = 3, h = 86) => `<div class="grid skel-grid" aria-busy="true" aria-label="Loading">
  ${Array.from({ length: cards }, () => `<div class="card skel" style="height:${h}px"></div>`).join("")}</div>`;
const lastValues = new Map();
/* Numbers marked data-count count up the first time they appear and pulse when a refresh changes them. */
function animate(root) {
  root.querySelectorAll("[data-count]").forEach((el) => {
    const key = el.dataset.key || el.dataset.count;
    const to = Number(el.dataset.count), fmt = el.dataset.fmt || "int";
    const show = (v) => (el.textContent = fmt === "pct" ? (v * 100).toFixed(Number(el.dataset.dp ?? 1)) + "%"
      : fmt === "dec" ? v.toFixed(1) : Math.round(v).toLocaleString());
    const prev = lastValues.get(key);
    lastValues.set(key, to);
    if (REDUCED || !Number.isFinite(to)) return show(to);
    if (prev !== undefined) {
      show(to);
      if (prev !== to) { el.classList.remove("pulse"); void el.offsetWidth; el.classList.add("pulse"); }
      return;
    }
    const t0 = performance.now(), ms = 650;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / ms);
      show(to * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}
const count = (v, key, fmt = "int", dp) => (v == null ? "—"
  : `<span data-count="${v}" data-key="${esc(key)}" data-fmt="${fmt}"${dp != null ? ` data-dp="${dp}"` : ""}>${fmt === "pct" ? (v * 100).toFixed(dp ?? 1) + "%" : Number(v).toLocaleString()}</span>`);
/* "x s ago" texts tick every second. */
const agoSpan = (iso) => `<span data-ago="${esc(iso || "")}">${ago(iso)}</span>`;
setInterval(() => document.querySelectorAll("[data-ago]").forEach((el) => { if (el.dataset.ago) el.textContent = ago(el.dataset.ago); }), 1000);
const crumbs = (here) => `<nav class="crumbs" aria-label="Breadcrumb"><a class="back" href="/insights">← Back</a>
  <ol><li><a href="/insights">Insights</a></li><li aria-current="page">${esc(here)}</li></ol></nav>`;
const notEnough = (what) => emptyCard("Not enough history yet", what);

/* ---------- night sky ---------- */
(function sky() {
  const c = $("#sky"), ctx = c.getContext("2d");
  let stars = [], w = 0, h = 0;
  function size() {
    const dpr = Math.min(2, devicePixelRatio || 1);
    w = c.clientWidth; h = c.clientHeight;
    c.width = w * dpr; c.height = h * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.round((w * h) / 9000);
    stars = Array.from({ length: n }, () => ({ x: Math.random() * w, y: Math.random() * h, r: Math.random() * 1.2 + 0.2,
      a: Math.random() * 0.6 + 0.2, p: Math.random() * Math.PI * 2, s: 0.4 + Math.random() * 1.2 }));
  }
  function draw(t) {
    ctx.clearRect(0, 0, w, h);
    for (const s of stars) {
      const a = REDUCED ? s.a : s.a * (0.65 + 0.35 * Math.sin(s.p + (t / 1000) * s.s));
      ctx.fillStyle = `rgba(214,226,255,${a.toFixed(3)})`;
      ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.fill();
    }
    if (!REDUCED && !document.hidden) requestAnimationFrame(draw);
  }
  size(); requestAnimationFrame(draw);
  addEventListener("resize", () => { size(); if (REDUCED) draw(0); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !REDUCED) requestAnimationFrame(draw); });
})();

/* ---------- tooltip ---------- */
const tip = $("#tip");
function showTip(html, x, y) {
  tip.innerHTML = html; tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(innerWidth - r.width - 8, Math.max(8, x + 14)) + "px";
  tip.style.top = Math.max(8, y - r.height - 12) + "px";
}
const hideTip = () => { tip.hidden = true; };

/* ---------- sparkline: one series, area + 2px line, crosshair tooltip ---------- */
function sparkline(points, { color, fmt, threshold }) {
  const pts = points.filter((p) => p.v != null);
  if (pts.length < 2) return `<svg class="spark" viewBox="0 0 300 64" aria-hidden="true"><text x="0" y="38" fill="#7d889c" font-size="12">collecting…</text></svg>`;
  const max = Math.max(threshold ?? 0, ...pts.map((p) => p.v)) || 1;
  const t0 = new Date(pts[0].ts).getTime(), t1 = new Date(pts[pts.length - 1].ts).getTime() || t0 + 1;
  const X = (p) => ((new Date(p.ts).getTime() - t0) / Math.max(1, t1 - t0)) * 300;
  const Y = (v) => 60 - (v / max) * 54;
  const line = pts.map((p, i) => (i ? "L" : "M") + X(p).toFixed(1) + " " + Y(p.v).toFixed(1)).join(" ");
  const area = `${line} L300 62 L0 62 Z`;
  const thr = threshold != null && threshold <= max ? `<line class="thr" x1="0" x2="300" y1="${Y(threshold)}" y2="${Y(threshold)}"/>` : "";
  const id = "s" + Math.random().toString(36).slice(2, 8);
  sparkData[id] = { pts, X, Y, fmt };
  return `<svg class="spark" id="${id}" viewBox="0 0 300 64" preserveAspectRatio="none" role="img" aria-label="trend, last 90 minutes">
    ${thr}<path class="area" d="${area}" fill="${color}"/><path class="line" d="${line}" stroke="${color}" vector-effect="non-scaling-stroke"/>
    <line class="cross" y1="0" y2="62" vector-effect="non-scaling-stroke"/><circle class="dot" r="4" fill="${color}" vector-effect="non-scaling-stroke"/></svg>`;
}
const sparkData = {};
document.addEventListener("pointermove", (e) => {
  const svg = e.target.closest?.("svg.spark");
  document.querySelectorAll("svg.spark.hovering").forEach((s) => { if (s !== svg) s.classList.remove("hovering"); });
  if (!svg || !sparkData[svg.id]) { if (!e.target.closest?.(".chart .hit")) hideTip(); return; }
  const { pts, X, Y, fmt } = sparkData[svg.id];
  const r = svg.getBoundingClientRect();
  const x = ((e.clientX - r.left) / r.width) * 300;
  let best = pts[0];
  for (const p of pts) if (Math.abs(X(p) - x) < Math.abs(X(best) - x)) best = p;
  svg.classList.add("hovering");
  const cx = X(best), cy = Y(best.v);
  $(".cross", svg).setAttribute("x1", cx); $(".cross", svg).setAttribute("x2", cx);
  $(".dot", svg).setAttribute("cx", cx); $(".dot", svg).setAttribute("cy", cy);
  showTip(`${clock(best.ts)} · <b>${esc(fmt(best.v))}</b>`, e.clientX, e.clientY);
});
document.addEventListener("pointerleave", hideTip);

/* ---------- Live ---------- */
const STATE = {
  ok: { c: "var(--good)", icon: "✓", label: "Healthy" },
  warn: { c: "var(--serious)", icon: "!", label: "" },
  down: { c: "var(--critical)", icon: "✕", label: "Unreachable" },
  wait: { c: "var(--muted)", icon: "…", label: "Waiting for the first poll" },
};
const POLL_NOTE = "Nightwarden polls Citebase every 10 minutes.";
function statusOf(s) {
  if (!s.last_poll) return { ...STATE.wait };
  if (!s.reachable) return { ...STATE.down, label: "Unreachable", note: s.last_error };
  if (s.open_incidents) return { ...STATE.warn, label: `${s.open_incidents} open incident${s.open_incidents === 1 ? "" : "s"}` };
  return { ...STATE.ok };
}
function tile(label, value, sub, href) {
  const body = `<div class="label">${label}</div><div class="big">${value}</div><div class="sub">${sub || "&nbsp;"}</div>`;
  return href ? `<a class="card clickable" href="${href}">${body}<span class="arrow" aria-hidden="true">→</span></a>`
    : `<div class="card">${body}</div>`;
}
const emptyCard = (title, body) => `<div class="card empty-card"><strong>${title}</strong><p>${body}</p></div>`;
const failCard = (what, e) => emptyCard(`Couldn't load ${what}`, `${esc(e?.message || e)}. Retrying in 2 minutes.`);
const pathText = (d) => d.decided_by || (d.degraded ? "rules" : "agent");
const pathBadges = (d) => `<span class="pill plain path">decided by ${esc(pathText(d))}</span>${d.degraded ? '<span class="pill degraded">degraded</span>' : ""}`;
const toolCount = (d) => d.tool_count ?? (d.tool_calls || []).filter((c) => c.tool !== "decide").length;
function decisionRow(d) {
  const n = toolCount(d);
  const href = d.incident_id ? `/incidents/${encodeURIComponent(d.incident_id)}` : null;
  const body = `<div class="card decision">
    <div class="row"><span class="pill ${esc(d.action)}">${esc(d.action)}</span>
      <span class="pill plain">${esc(d.confidence)} confidence</span>${pathBadges(d)}
      <span class="muted">${when(d.ts)}${d.event ? " · " + esc(d.event.kind.replaceAll("_", " ")) : ""}</span></div>
    <p><strong>${esc(d.summary)}</strong></p>
    <div class="sub">${n} tool call${n === 1 ? "" : "s"} · ${(d.latency_ms / 1000).toFixed(1)} s${href ? " · open the incident to see the investigation" : ""}</div></div>`;
  return href ? `<a class="row-link" href="${href}">${body}</a>` : body;
}
function heroHtml(s) {
  const k = statusOf(s);
  return `<div class="card hero clickable" style="--c:${k.c}">
      <a class="stretch" href="/health" aria-label="Health checks"></a><span class="arrow" aria-hidden="true">→</span>
      <div class="ring" role="img" aria-label="${esc(k.label)}">
        ${REDUCED || k.icon === "…" ? "" : '<div class="sweep"></div>'}
        <svg viewBox="0 0 92 92" aria-hidden="true"><circle cx="46" cy="46" r="40" fill="none" stroke="var(--grid)" stroke-width="4"/>
          <circle cx="46" cy="46" r="40" fill="none" stroke="${k.c}" stroke-width="4" stroke-dasharray="6 6" opacity=".9"/></svg>
        <div class="icon">${k.icon}</div>
      </div>
      <div>
        <h1>${esc(k.label)}</h1>
        ${k.note ? `<div class="sub mono">${esc(k.note)}</div>` : ""}
        ${!s.last_poll ? `<div class="sub">${POLL_NOTE} The first reading will appear here.</div>` : ""}
        <div class="meta">
          <span>watching ${esc(s.target_host || s.service)}</span>
          <span>last poll ${agoSpan(s.last_poll)}</span>
          <a class="above" href="/usage">AI triage: ${esc(s.ai_triage)}${s.last_triage_degraded ? " (last decision used rules)" : ""}</a>
          ${s.email_alerts === "off" ? `<span class="warn">email alerts: off</span>` : ""}
          ${s.commit ? `<span class="mono">commit ${esc(String(s.commit).slice(0, 7))}</span>` : ""}
        </div>
      </div>
    </div>`;
}
function glanceHtml(st) {
  const polls = st.uptime_7d?.polls ?? 0;
  if (!polls && !st.incidents_7d && !st.resolved_30d) {
    return emptyCard("No readings yet", `Uptime and incident numbers appear after the first successful poll. ${POLL_NOTE}`);
  }
  const up = (u, key) => (u?.pct == null ? "—" : count(u.pct, key, "pct", u.pct === 1 ? 0 : 2));
  const polled = (u) => (u?.polls ? `${u.polls} poll${u.polls === 1 ? "" : "s"}` : "no polls in this window");
  return `<div class="grid tiles">
      ${tile("Uptime · 24 h", up(st.uptime_24h, "up24"), polled(st.uptime_24h), "/reports")}
      ${tile("Uptime · 7 days", up(st.uptime_7d, "up7"), polled(st.uptime_7d), "/reports")}
      ${tile("Incidents · 7 days", count(st.incidents_7d, "inc7"), st.incidents_7d ? `${st.pages_7d} paged · ${st.open_incidents} open` : "All quiet", "/incidents")}
      ${tile("Time to resolve", dur(st.mttr_s), st.resolved_30d ? `average of ${st.resolved_30d} in 30 days` : "no resolved incidents yet", "/reports")}
    </div>`;
}
function signalsHtml(s) {
  const series = s.series || [];
  const col = (key) => series.map((p) => ({ ts: p.ts, v: p[key] }));
  const has = (key) => series.filter((p) => p[key] != null).length;
  const keys = ["requests_5m", "error_rate", "fallback_rate", "latency_p95_ms"];
  const most = Math.max(0, ...keys.map(has));
  if (most < 2) {
    return emptyCard("Not enough data for trends yet",
      `Trends need at least two successful polls in the last 90 minutes; ${most ? "there is 1 so far" : "there are none yet"}. ${POLL_NOTE}`);
  }
  const w = s.window || {};
  const card = (label, key, big, opts, note) => `<div class="card spark-card clickable">
      <a class="stretch" href="/performance" aria-label="${label}: agent performance"></a><span class="arrow" aria-hidden="true">→</span>
      <div class="label">${label}</div><div class="big">${big}</div>
      ${has(key) >= 2 ? sparkline(col(key), opts) : '<p class="sub nodata">No data for this signal in the last 90 minutes.</p>'}
      ${note ? `<div class="sub">${note}</div>` : ""}</div>`;
  return `<div class="grid">
      ${card("Requests per window", "requests_5m", w.requests_5m ?? "—", { color: "var(--agent)", fmt: (v) => v + " requests" })}
      ${card("Server error rate", "error_rate", pct(w.error_rate), { color: "var(--critical)", fmt: (v) => pct(v), threshold: 0.05 }, "dashed line: 5 % alert threshold")}
      ${card("Fallback answers", "fallback_rate", pct(w.fallback_rate), { color: "var(--warning)", fmt: (v) => pct(v), threshold: 0.2 }, "dashed line: 20 % alert threshold")}
      ${card("p95 latency", "latency_p95_ms", w.latency_p95_ms != null ? Math.round(w.latency_p95_ms) + " ms" : "—",
        { color: "var(--good)", fmt: (v) => Math.round(v) + " ms", threshold: 5000 }, "dashed line: 5 s alert threshold")}
    </div>`;
}
async function renderLive() {
  const el = $("#live");
  if (!el.innerHTML) el.innerHTML = skeleton(1, 132) + "<h2>At a glance</h2>" + skeleton(4) + "<h2>Signals</h2>" + skeleton(4, 140);
  const [s, st, decisions] = await Promise.allSettled([get("/api/status"), get("/api/stats"), get("/api/decisions?limit=6&brief=1")]);
  if (s.status === "fulfilled") $("#svc").textContent = s.value.service;
  el.innerHTML = `
    ${s.status === "fulfilled" ? heroHtml(s.value) : failCard("status", s.reason)}
    <h2>At a glance</h2>
    ${st.status === "fulfilled" ? glanceHtml(st.value) : failCard("headline numbers", st.reason)}
    <h2>Signals · last 90 minutes</h2>
    ${s.status === "fulfilled" ? signalsHtml(s.value) : failCard("signals", s.reason)}
    <h2>Recent decisions</h2>
    ${decisions.status !== "fulfilled" ? failCard("decisions", decisions.reason)
      : decisions.value.length ? `<div class="list">${decisions.value.map(decisionRow).join("")}</div>`
      : emptyCard("No decisions yet. All quiet.", "Nothing has needed triage. Detectors fire on errors, latency, fallback answers, empty retrieval and traffic anomalies.")}`;
  animate(el);
}

/* ---------- Incidents + investigation trace ---------- */
let incFilter = "all";
const ACTION_TEXT = {
  page: "Wake the on-call engineer now.",
  notify: "Post to the team channel and handle it during working hours.",
  watch: "No action yet. Re-check if it happens again.",
  suppress: "No action needed: expected or harmless.",
};
const msTxt = (ms) => (ms == null ? "" : ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s");
const argsLine = (a) => {
  const e = Object.entries(a || {});
  return e.length ? e.map(([k, v]) => `${k}=${typeof v === "string" ? JSON.stringify(v) : JSON.stringify(v)}`).join(", ") : "no arguments";
};
function traceStep(c) {
  const args = argsLine(c.args);
  const result = c.result_summary ? esc(c.result_summary)
    : c.result_hidden ? '<span class="muted">result recorded (admin only)</span>' : '<span class="muted">no result recorded</span>';
  return `<li class="tstep"><div class="thead"><strong>${esc(c.tool.replaceAll("_", " "))}</strong><span class="dur">${msTxt(c.duration_ms)}</span></div>
    <div class="targs mono" title="${esc(args)}">${esc(args)}</div>
    <div class="tres">→ ${result}</div>
    ${c.result_preview ? `<details><summary class="sub">raw result</summary><pre>${esc(c.result_preview)}</pre></details>` : ""}</li>`;
}
function traceHtml(d) {
  const calls = d.tool_calls || [];
  const tools = calls.filter((c) => c.tool !== "decide");
  const decide = [...calls].reverse().find((c) => c.tool === "decide");
  const signal = d.event ? `<li class="tstep signal"><div class="thead"><strong>signal: ${esc(d.event.kind.replaceAll("_", " "))}</strong><span class="dur">${clock(d.ts)}</span></div>
      ${d.event.detail ? `<div class="tres">${esc(d.event.detail)}</div>` : ""}</li>` : "";
  const steps = tools.length ? tools.map(traceStep).join("")
    : `<li class="tstep rules"><div class="thead"><strong>${d.degraded ? "rules baseline" : "no evidence gathered"}</strong></div>
        <div class="tres">${d.degraded ? "AI triage was unavailable, so no tools ran. The rules decided from the signal's numbers alone." : "The agent decided without calling any tools."}</div></li>`;
  return `<ol class="trace">${signal}${steps}
    <li class="tstep final"><div class="thead"><strong>decision</strong><span class="dur">${msTxt(decide?.duration_ms)}</span></div>
      <dl class="verdict">
        <dt>Cause</dt><dd><strong>${esc(d.summary)}</strong>${d.rationale ? `<p class="sub">${esc(d.rationale)}</p>` : ""}</dd>
        <dt>Severity</dt><dd class="row"><span class="pill ${esc(d.action)}">${esc(d.action)}</span><span class="sub">${esc(d.confidence || "")} confidence</span></dd>
        <dt>Recommended action</dt><dd>${esc(ACTION_TEXT[d.action] || d.action)}</dd>
      </dl></li></ol>`;
}
function runCard(d, i, n) {
  return `<div class="card run">
    <div class="row run-head"><strong>${n > 1 ? `Triage run ${i + 1} of ${n}` : "Triage run"}</strong>${pathBadges(d)}
      <span class="sub">${when(d.ts)} · ${toolCount(d)} tool call${toolCount(d) === 1 ? "" : "s"} · ${(d.latency_ms / 1000).toFixed(1)} s total</span></div>
    ${traceHtml(d)}</div>`;
}
async function renderIncident(el, id) {
  let i;
  try {
    i = await get("/api/incidents/" + encodeURIComponent(id));
  } catch (e) {
    const missing = String(e.message).endsWith("404");
    document.title = "Nightwarden";
    el.innerHTML = `<p><a href="/incidents">← All incidents</a></p>` + (missing
      ? emptyCard("Incident not found", "This link doesn't match any incident. It may be mistyped, or the incident was removed.")
      : failCard("this incident", e));
    return;
  }
  const decisions = i.decisions || [];
  const latest = decisions[decisions.length - 1];
  document.title = `${i.severity.toUpperCase()} · ${i.summary} · Nightwarden`;
  const url = location.origin + "/incidents/" + encodeURIComponent(i.id);
  el.innerHTML = `<p><a href="/incidents">← All incidents</a></p>
    <div class="card inc-head">
      <div class="row"><span class="pill ${esc(i.severity)}">${esc(i.severity)}</span><span class="pill ${esc(i.status)}">${esc(i.status)}</span>
        ${latest ? pathBadges(latest) : ""}</div>
      <h1>${esc(i.summary)}</h1>
      <div class="meta"><span>opened ${when(i.opened_at)}</span><span class="mono">${esc(i.kind)}</span>
        <span>${i.occurrences} occurrence${i.occurrences === 1 ? "" : "s"}</span>${i.closed_at ? `<span>resolved ${when(i.closed_at)}</span>` : ""}</div>
      <div class="share"><button class="btn" id="copy">Copy link</button><span class="mono muted" id="link">${esc(url)}</span></div>
    </div>
    <h2>Investigation</h2>
    ${decisions.length ? `<div class="list">${decisions.map((d, k) => runCard(d, k, decisions.length)).join("")}</div>`
      : emptyCard("No triage runs recorded yet", "This incident has no decisions attached, so there is no trace to show.")}`;
  $("#copy").onclick = async () => {
    try { await navigator.clipboard.writeText(url); $("#copy").textContent = "Copied ✓"; }
    catch { getSelection().selectAllChildren($("#link")); $("#copy").textContent = "Press Ctrl+C to copy"; }
    setTimeout(() => { const b = $("#copy"); if (b) b.textContent = "Copy link"; }, 2000);
  };
}
async function renderIncidents(openId) {
  const el = $("#incidents");
  el.innerHTML = skeleton(3, 70);
  if (openId) return renderIncident(el, openId);
  document.title = "Incidents · Nightwarden";
  try {
    const list = await get("/api/incidents?limit=100" + (incFilter === "all" ? "" : "&status=" + incFilter));
    const none = { all: ["No incidents yet. All quiet.", "An incident opens when triage decides a signal needs a page, a notification or watching."],
      open: ["No open incidents. All quiet.", "Everything that was opened has been resolved."],
      resolved: ["No resolved incidents yet.", "Incidents resolve themselves after 3 quiet hours, or by hand."] }[incFilter];
    el.innerHTML = `<div class="filters" role="group" aria-label="Filter incidents">
        ${["all", "open", "resolved"].map((f) => `<button class="btn" data-f="${f}" aria-pressed="${incFilter === f}">${f[0].toUpperCase() + f.slice(1)}</button>`).join("")}
      </div>` + (list.length ? `<div class="list">${list.map((i) => `<a class="row-link" href="/incidents/${encodeURIComponent(i.id)}"><div class="card">
          <div class="row"><span class="pill ${esc(i.severity)}">${esc(i.severity)}</span><span class="pill ${esc(i.status)}">${esc(i.status)}</span>
          <strong>${esc(i.summary)}</strong></div>
          <div class="sub" style="margin-top:4px">${esc(i.kind.replaceAll("_", " "))} · opened ${ago(i.opened_at)} · ${i.occurrences}×</div></div></a>`).join("")}</div>`
      : emptyCard(...none));
    el.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { incFilter = b.dataset.f; renderIncidents(); }));
  } catch (e) {
    el.innerHTML = failCard("incidents", e);
  }
}

/* ---------- Evaluation ---------- */
let evalTable = false;
function metricBars(b, a) {
  const rows = [["page_recall", "Real emergencies paged"], ["silent_recall", "Silent failures caught"], ["accuracy", "Right action overall"],
    ["page_precision", "Pages that were real"], ["loud_accuracy", "Loud failures, right action"]];
  return rows.map(([k, l]) => `<div class="mbar"><div>${l}</div><div class="tracks">
      <div class="track"><div class="bar"><span style="width:${(a[k] ?? 0) * 100}%;background:var(--agent)"></span></div><div class="v">${pct(a[k], 0)}</div></div>
      <div class="track"><div class="bar"><span style="width:${(b[k] ?? 0) * 100}%;background:var(--rules)"></span></div><div class="v">${pct(b[k], 0)}</div></div>
    </div></div>`).join("");
}
function scenarioChart(rows) {
  const W = 960, H = 280, L = 34, B = 64, T = 10, R = 8;
  const n = rows.length, gw = (W - L - R) / n, bw = Math.min(18, gw / 3.2), max = rows[0]?.n || 4;
  const Y = (v) => T + (H - T - B) * (1 - v / max);
  let g = "";
  for (let v = 0; v <= max; v++) g += `<line class="gridline" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${v}</text>`;
  rows.forEach((r, i) => {
    const cx = L + gw * i + gw / 2;
    const bar = (v, x, color) => {
      const y = Y(v), h = Math.max(0, H - B - y);
      return h ? `<path d="M${x} ${H - B} V${y + 4} q0 -4 4 -4 h${bw - 8} q4 0 4 4 V${H - B} Z" fill="${color}"/>` : "";
    };
    g += `<rect class="hit" data-i="${i}" x="${L + gw * i}" y="${T}" width="${gw}" height="${H - T - B}"/>
      <rect class="hl" x="${L + gw * i + 2}" y="${T}" width="${gw - 4}" height="${H - T - B}" rx="6"/>
      ${bar(r.agent_correct, cx - bw - 1, "var(--agent)")}${bar(r.baseline_correct, cx + 1, "var(--rules)")}
      <text x="${cx}" y="${H - B + 16}" text-anchor="middle">${r.scenario}</text>
      ${r.silent ? `<text class="silent-tag" x="${cx}" y="${H - B + 31}" text-anchor="middle">silent</text>` : ""}`;
  });
  g += `<line class="base" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>
    <text x="${L}" y="${H - 12}">scenario · bars show correct decisions out of ${max} variations</text>`;
  return `<svg class="chart" id="scen" viewBox="0 0 ${W} ${H}" role="img" aria-label="Correct decisions per scenario, agent versus rules">${g}</svg>`;
}
async function renderEval(el = $("#eval"), head = crumbs("Evaluation")) {
  if (!el.innerHTML) el.innerHTML = head + skeleton(2, 120);
  try {
    const r = await get("/api/eval");
    if (!r.available) { el.innerHTML = head + emptyCard("No evaluation results published yet", "Results appear here after the eval runs (python -m eval.run_eval)."); return; }
    const s = r.summary, b = s.baseline || {}, a = s.agent || {};
    if (!a.n) { el.innerHTML = head + emptyCard("Only the rules baseline has been scored so far", "The AI agent's numbers appear once an eval run with a model key has finished."); return; }
    const c = s.agent_cost || {};
    el.innerHTML = `${head}
      <h1>Agent vs. a frozen rules baseline</h1>
      <p class="sub">${b.n} held-out cases: 12 failure scenarios × variations ${(s.reps || []).join(", ")}, never inspected before the run.
        Labels and the rules were frozen before the agent was written. Model <span class="mono">${esc(r.model)}</span>, run ${when(r.generated_at)}.</p>
      <div class="card" style="margin-top:14px">
        <div class="legend" style="margin-bottom:6px"><span class="key" style="--k:var(--agent)">AI agent</span><span class="key" style="--k:var(--rules)">Rules baseline</span></div>
        ${metricBars(b, a)}
      </div>
      <h2>Cost per decision</h2>
      <div class="grid tiles">
        ${tile("Tool calls (median)", c.median_tool_calls ?? "—", "evidence gathered before deciding")}
        ${tile("Tokens (mean)", c.mean_tokens ? c.mean_tokens.toLocaleString() : "—", "per event")}
        ${tile("Decision time (p95)", c.p95_latency_ms ? (c.p95_latency_ms / 1000).toFixed(1) + " s" : "—", "includes free-tier rate-limit waits")}
        ${tile("Inconclusive", c.inconclusive ?? 0, "ran out of rounds")}
      </div>
      <h2>Per scenario</h2>
      <div class="card">
        <div class="row" style="justify-content:space-between;margin-bottom:8px">
          <div class="legend"><span class="key" style="--k:var(--agent)">AI agent</span><span class="key" style="--k:var(--rules)">Rules baseline</span>
            <span class="muted">· “silent” = the service answers 200s while quality drops</span></div>
          <button class="btn" id="tbl" aria-pressed="${evalTable}">${evalTable ? "Show chart" : "Show table"}</button>
        </div>
        <div id="scenario-view">${evalTable ? scenarioTable(s.per_scenario) : scenarioChart(s.per_scenario)}</div>
      </div>
      <p class="sub" style="margin-top:12px">Weak spot, stated plainly: the agent never dismisses harmless noise (0 of 12 benign cases suppressed) and over-pages a quarter of notify-level problems.</p>`;
    $("#tbl", el).onclick = () => { evalTable = !evalTable; renderEval(el, head); };
    const svg = $("#scen", el);
    if (svg) svg.querySelectorAll(".hit").forEach((h) => {
      const p = s.per_scenario[+h.dataset.i];
      const show = (e) => showTip(`<b>${p.scenario}. ${esc(p.name.replaceAll("_", " "))}</b>${p.silent ? " · silent" : ""}<br>expected: ${esc(p.expected)}<br>
        AI agent <b>${p.agent_correct}/${p.n}</b> · rules <b>${p.baseline_correct}/${p.n}</b>`, e.clientX, e.clientY);
      h.addEventListener("pointermove", show); h.addEventListener("pointerleave", hideTip);
    });
  } catch (e) {
    el.innerHTML = head + failCard("evaluation", e);
  }
}
function scenarioTable(rows) {
  return `<div class="scroll"><table><thead><tr><th>#</th><th>Scenario</th><th>Expected</th><th class="num">AI agent</th><th class="num">Rules</th><th>Agent chose</th></tr></thead><tbody>
    ${rows.map((p) => `<tr><td>${p.scenario}</td><td>${esc(p.name.replaceAll("_", " "))}${p.silent ? ' <span class="pill plain">silent</span>' : ""}</td>
      <td><span class="pill ${esc(p.expected)}">${esc(p.expected)}</span></td><td class="num">${p.agent_correct}/${p.n}</td><td class="num">${p.baseline_correct}/${p.n}</td>
      <td class="sub">${Object.entries(p.agent_actions || {}).map(([k, v]) => `${esc(k)} ×${v}`).join(", ")}</td></tr>`).join("")}</tbody></table></div>`;
}

/* ---------- How it works ---------- */
function renderHow(el) {
  if (el.dataset.done) return;
  el.dataset.done = "1";
  const node = (x, y, w, title, sub, cls = "") => `<g class="node ${cls}"><rect x="${x}" y="${y}" width="${w}" height="62" rx="12"/>
    <text x="${x + 14}" y="${y + 26}">${title}</text><text class="s" x="${x + 14}" y="${y + 45}">${sub}</text></g>`;
  const wire = (d, warn) => `<path class="wire" d="${d}"/><path class="flow${warn ? " warn" : ""}" d="${d}"/>`;
  el.innerHTML = `
    <h1 id="how">How Nightwarden works</h1>
    <p class="sub">A scheduler wakes it every 10 minutes. It reads Citebase's health, decides whether anything looks wrong, and if so an AI agent investigates and decides who needs to know.</p>
    <div class="card" style="margin-top:14px"><div class="scroll">
      <svg class="diagram" viewBox="0 0 1000 410" role="img" aria-label="Pipeline: Citebase metrics and logs, poller, detectors, AI agent with tools, incidents, Discord and dashboard; rules take over if the AI is down">
        ${wire("M190 71 H250")}${wire("M430 71 H490")}${wire("M670 71 H720 Q740 71 740 91 V150")}
        ${wire("M840 212 V250 Q840 270 820 270 H690")}${wire("M500 270 H460")}${wire("M595 302 V330")}
        ${wire("M580 102 V150", true)}${wire("M580 212 V240", true)}
        ${node(20, 40, 170, "Citebase", "metrics + redacted logs")}
        ${node(250, 40, 180, "Poller", "every 10 min · deltas")}
        ${node(490, 40, 180, "Detectors", "errors · fallbacks · latency")}
        ${node(700, 150, 280, "AI agent", "logs · metrics · deploys · history → decide", "ai")}
        ${node(500, 150, 160, "Rules fallback", "if the AI is down: fail loud", "fail")}
        ${node(500, 240, 190, "Incident", "merge 6 h · auto-resolve 3 h")}
        ${node(250, 240, 210, "Discord · email", "page = @here · notify")}
        ${node(510, 330, 170, "This dashboard", "public · read-only")}
        ${node(20, 170, 200, "healthchecks.io", "pinged every poll")}
        ${wire("M330 102 V140 Q330 200 220 200")}
      </svg></div></div>
    <h2>Step by step</h2>
    <ol class="howsteps">
      <li><div class="card"><strong>Poll</strong><p class="sub">cron-job.org calls the poll endpoint. Nightwarden reads Citebase's request counters, latency, fallback answers and retrieval stats, and notices deploys and restarts.</p></div></li>
      <li><div class="card"><strong>Detect</strong><p class="sub">Counters become rates. Detectors fire on server errors, 4xx storms, slow answers, fallback spikes, empty retrieval and traffic jumps or drops, with a 30-minute cooldown.</p></div></li>
      <li><div class="card"><strong>Investigate</strong><p class="sub">The AI agent reads logs, metric history, deploys and past incidents with read-only tools, then must call <span class="mono">decide</span>: page, notify, watch or suppress.</p></div></li>
      <li><div class="card"><strong>Fail loud</strong><p class="sub">If the AI is unavailable, rules decide and are raised, so silent failures still page. Nightwarden never goes quiet because its own AI broke.</p></div></li>
      <li><div class="card"><strong>Alert once</strong><p class="sub">Repeats merge into one incident; alerts go out only when it opens or gets worse. Quiet incidents resolve themselves after 3 hours.</p></div></li>
      <li><div class="card"><strong>Watch the watcher</strong><p class="sub">Every poll pings healthchecks.io. If polling ever stops, that service emails the owner.</p></div></li>
    </ol>`;
}


/* ---------- Health: Nightwarden's own self-checks ---------- */
const CHECK = { pass: "Pass", warn: "Warning", fail: "Failing" };
const OVERALL = { pass: "All checks pass", warn: "Working, with warnings", fail: "Something needs attention" };
async function renderHealth() {
  const el = $("#health");
  const head = crumbs("Health");
  if (!el.innerHTML) el.innerHTML = head + skeleton(1, 60) + "<h2>Checks</h2>" + skeleton(6, 64);
  let h;
  try { h = await get("/api/health/checks"); } catch (e) { el.innerHTML = `${head}<h1>Health</h1>${failCard("health checks", e)}`; return; }
  const checks = h.checks || [];
  el.innerHTML = `${head}
    <h1>Health</h1>
    <p class="sub">Nightwarden checking itself: polling, alerting, AI triage and its database. Refreshed every 2 minutes.</p>
    <div class="card overall ${esc(h.overall)}"><span class="pill ${esc(h.overall)}">${esc(CHECK[h.overall] || h.overall)}</span>
      <strong>${esc(OVERALL[h.overall] || "")}</strong><span class="muted">checked ${agoSpan(h.checked_at)}</span></div>
    <h2>Checks</h2>
    ${checks.length ? `<div class="list checks">${checks.map((c) => `<div class="card check">
        <span class="pill ${esc(c.status)}">${esc(CHECK[c.status] || c.status)}</span>
        <div class="check-body"><strong>${esc(c.name)}</strong><div class="sub">${esc(c.detail)}</div></div></div>`).join("")}</div>`
      : emptyCard("No checks returned", "The server answered without any checks. Try again in a minute.")}`;
}

/* ---------- Usage: free-tier budgets ---------- */
const num = (v) => (v == null ? "—" : Number(v).toLocaleString());
const day = (iso) => (iso ? new Date(iso).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "");
const TODAY = "today (UTC, resets 05:30 IST)";
function meter(label, used, limit, { sub = "", warnAt = 0.8, failAt = 1, unit = "" } = {}) {
  if (limit == null || used == null) return "";
  const scale = Math.max(used, limit) * 1.15 || 1;
  const share = limit ? used / limit : 1;
  const tone = share >= failAt ? "fail" : share >= warnAt ? "warn" : "pass";
  return `<div class="meter">
    <div class="meter-head"><span>${label}</span><span class="mono">${num(used)}${unit} / ${num(limit)}${unit} · ${Math.round(share * 100)}%</span></div>
    <div class="meter-bar" role="meter" aria-valuemin="0" aria-valuemax="${limit}" aria-valuenow="${used}" aria-label="${esc(label)}">
      <span class="fill ${tone}" style="width:${((used / scale) * 100).toFixed(1)}%"></span>
      <span class="limit" style="left:${((limit / scale) * 100).toFixed(1)}%" title="limit ${num(limit)}"></span>
    </div>
    ${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}
async function renderUsage() {
  const el = $("#usage");
  const head = crumbs("Usage");
  if (!el.innerHTML) el.innerHTML = head + skeleton(4, 120);
  let u;
  try { u = await get("/api/usage"); } catch (e) { el.innerHTML = `${head}<h1>Usage</h1>${failCard("usage", e)}`; return; }
  const g = u.groq, p = u.polls, m = u.emails, n = u.neon;
  const left = g.triages_left_today == null
    ? "No AI triage recorded yet, so there is no per-triage average to estimate from."
    : `About <b>${num(g.triages_left_today)}</b> AI triages left today (≈${num(g.mean_tokens_per_triage)} tokens each, from the last ${g.triage_sample}).`;
  const neon = !n.configured ? emptyCard("Neon usage: not configured", "Set NEON_API_KEY and NEON_PROJECT_ID to show network transfer and compute hours here.")
    : n.error ? emptyCard("Couldn't read Neon usage", esc(n.error))
    : `<div class="card">
        ${n.transfer ? meter("Network transfer · this month", n.transfer.gb, n.transfer.limit_gb, { unit: " GB", warnAt: 0.7, failAt: 0.9, sub: "warn at 70 %, fail at 90 %" }) : ""}
        ${meter("Compute hours · billing period", n.cu_hours, n.limit, { unit: " CU-h", sub: n.period_start ? `since ${when(n.period_start)}` : "" })}</div>`;
  const tracking = m.tracking_since ? `tracking since ${day(m.tracking_since)}` : "tracking since the first alert email (none recorded yet)";
  el.innerHTML = `${head}
    <h1>Usage</h1>
    <p class="sub">Free-tier budgets, from what Nightwarden already records. The dashed mark on each bar is the limit. Updated every 5 minutes.</p>
    <h2>Groq tokens</h2>
    <div class="card">
      ${meter(TODAY, g.today.total, g.limit_per_day, { sub: `${num(g.today.live)} live · ${num(g.today.eval)} evaluation` })}
      <p class="sub">${left}</p>
      ${meter("Month to date", g.month.total, g.limit_per_day * new Date(u.generated_at).getUTCDate(), { sub: `${num(g.month.live)} live · ${num(g.month.eval)} evaluation · limit is the daily cap × days so far` })}
    </div>
    <h2>Polls</h2>
    <div class="card">
      ${p.month ? meter(TODAY, p.today, p.limit_per_day, { warnAt: 1.01, sub: "one every 10 minutes is 144 a day" })
        + meter("Month to date", p.month, p.month_limit, { warnAt: 1.01, sub: p.expected_since ? `expected: one every 10 minutes since ${when(p.expected_since)}` : "" })
        : emptyCard("No polls yet", "Poll counts appear after the first poll.")}
    </div>
    <h2>Alert emails</h2>
    <div class="card">
      <p class="sub">${tracking}</p>
      ${m.month || m.month_failed ? meter(TODAY, m.today, m.limit_per_day, { sub: m.today_failed ? `${m.today_failed} failed today` : "" })
        + meter("Month to date", m.month, m.month_limit, { sub: m.month_failed ? `${m.month_failed} failed this month` : "" })
        : emptyCard("No alert emails this month", "Emails go out for pages only. The Brevo free plan allows 300 a day.")}
    </div>
    <h2>Neon</h2>
    ${neon}`;
}

/* ---------- Insights hub ---------- */
const HUB = [
  { path: "/performance", title: "Performance", blurb: "How the agent decides: paths, speed, cost, agreement with the rules." },
  { path: "/reports", title: "Reports", blurb: "Week, month and year: uptime, incidents, time to resolve, pages." },
  { path: "/health", title: "Health", blurb: "Nightwarden checking itself: polling, alerts, AI, database." },
  { path: "/usage", title: "Usage", blurb: "Free-tier budgets: Groq tokens, polls, emails, Neon." },
  { path: "/eval", title: "Evaluation", blurb: "The agent against a frozen rules baseline on labelled failures." },
];
const MINI = {
  "/performance": async () => {
    const p = await get("/api/performance?days=7");
    return p.decisions ? [count(p.decisions, "hub-dec"), `decisions in 7 days · ${pct(p.degraded_pct, 0)} degraded`]
      : ["—", "No decisions in the last 7 days yet"];
  },
  "/reports": async () => {
    const r = await get("/api/reports?period=week");
    return r.summary.polls ? [count(r.summary.uptime, "hub-up", "pct", 2), `uptime this week · ${r.summary.incidents} incident${r.summary.incidents === 1 ? "" : "s"}`]
      : ["—", "Not enough history yet: reports start after the first day is rolled up"];
  },
  "/health": async () => {
    const h = await get("/api/health/checks");
    const bad = h.checks.filter((c) => c.status !== "pass").length;
    return [`<span class="pill ${esc(h.overall)}">${esc(CHECK[h.overall])}</span>`, bad ? `${bad} of ${h.checks.length} checks need a look` : `all ${h.checks.length} checks pass`];
  },
  "/usage": async () => {
    const u = await get("/api/usage");
    return [count(u.groq.today.total / u.groq.limit_per_day, "hub-groq", "pct", 0), "of today's Groq tokens used"];
  },
  "/eval": async () => {
    const r = await get("/api/eval");
    const a = r.available && r.summary.agent;
    return a?.n ? [count(a.accuracy, "hub-eval", "pct", 0), `right action on ${a.n} held-out cases (rules: ${pct(r.summary.baseline.accuracy, 0)})`]
      : ["—", "No agent evaluation published yet"];
  },
};
async function renderInsights() {
  const el = $("#insights");
  el.innerHTML = `<h1>Insights</h1><p class="sub">Everything Nightwarden measures about the service and about itself.</p>
    <div class="hub">${HUB.map((c) => `<a class="card clickable hub-card" href="${c.path}" data-mini="${c.path}">
      <div class="label">${c.title}</div><div class="big mini-big"><span class="skel-line"></span></div>
      <div class="sub mini-sub"><span class="skel-line short"></span></div><p class="sub">${c.blurb}</p>
      <span class="arrow" aria-hidden="true">→</span></a>`).join("")}</div>`;
  await Promise.all(HUB.map(async (c) => {
    const card = el.querySelector(`[data-mini="${c.path}"]`);
    let big, sub;
    try { [big, sub] = await MINI[c.path](); } catch { [big, sub] = ["—", "Couldn't load right now"]; }
    if (!card.isConnected) return;
    card.querySelector(".mini-big").innerHTML = big;
    card.querySelector(".mini-sub").textContent = sub;
    animate(card);
  }));
}

/* ---------- Performance ---------- */
let perfDays = 7;
const seg = (name, options, current) => `<div class="seg" role="group" aria-label="${esc(name)}">${options.map(([v, l]) =>
  `<button class="btn" data-v="${v}" aria-pressed="${String(v) === String(current)}">${l}</button>`).join("")}</div>`;
function hbars(entries, total, colors = {}) {
  if (!total) return "";
  return `<div class="hbars">${entries.map(([k, v]) => `<div class="hbar"><span class="hl-label">${esc(k)}</span>
    <span class="hb-track"><span class="hb-fill" style="width:${((v / total) * 100).toFixed(1)}%;background:${colors[k] || "var(--agent)"}"></span></span>
    <span class="v mono">${v} · ${Math.round((v / total) * 100)}%</span></div>`).join("")}</div>`;
}
const ACTION_COLOR = { page: "var(--critical)", notify: "var(--serious)", watch: "var(--info)", suppress: "var(--muted)" };
const PATH_COLOR = { primary: "var(--agent)", backup: "var(--warning)", rules: "var(--rules)" };
async function renderPerformance() {
  const el = $("#performance");
  const head = crumbs("Performance");
  el.innerHTML = `${head}<h1>Agent performance</h1>${seg("Window", [[7, "7 days"], [30, "30 days"], [365, "365 days"]], perfDays)}<div id="perf-body">${skeleton(4)}</div>`;
  el.querySelectorAll(".seg [data-v]").forEach((b) => (b.onclick = () => { perfDays = +b.dataset.v; renderPerformance(); }));
  const body = $("#perf-body", el);
  let p;
  try { p = await get(`/api/performance?days=${perfDays}`); } catch (e) { body.innerHTML = failCard("performance", e); return; }
  if (!p.decisions) {
    body.innerHTML = notEnough(`No decisions in the last ${perfDays} days. Performance appears once signals have been triaged.`);
    return;
  }
  const d = p.disagreement;
  body.innerHTML = `
    <div class="grid tiles">
      ${tile("Decisions", count(p.decisions, "perf-n"), `last ${perfDays} days`)}
      ${tile("Degraded", count(p.degraded_pct, "perf-deg", "pct", 0), "decided without Groq")}
      ${tile("Decision time", msTxt(Math.round(p.decision_ms.median)) || "—", `median · p95 ${msTxt(p.decision_ms.p95) || "—"}`)}
      ${tile("Tool calls", p.tool_calls_median ?? "—", "median per AI decision")}
      ${tile("Tokens", p.mean_tokens == null ? "—" : count(p.mean_tokens, "perf-tok"), "mean per AI decision")}
      ${tile("Inconclusive", p.inconclusive_pct == null ? "—" : count(p.inconclusive_pct, "perf-inc", "pct", 0), "ran out of rounds")}
    </div>
    <h2>Decisions by action</h2>
    <div class="card">${hbars(Object.entries(p.by_action).sort((a, b) => b[1] - a[1]), p.decisions, ACTION_COLOR)}</div>
    <h2>Who decided</h2>
    <div class="card">${hbars(["primary", "backup", "rules"].map((k) => [k, p.decided_by[k] || 0]), p.decisions, PATH_COLOR)}
      <p class="sub">primary = Groq · backup = the backup AI provider · rules = no AI was available</p></div>
    <h2>Agent vs. rules (shadow baseline)</h2>
    ${d.compared ? `<div class="card"><div class="big">${count(d.pct, "perf-dis", "pct", 0)}</div>
      <div class="sub">of ${d.compared} AI decisions differ from what the rules alone would have said</div>
      ${d.top_kinds.length ? `<table class="compact"><thead><tr><th>Signal</th><th class="num">Disagreed</th></tr></thead><tbody>
        ${d.top_kinds.map((k) => `<tr><td>${esc(k.kind.replaceAll("_", " "))}</td><td class="num">${k.n} of ${k.of}</td></tr>`).join("")}</tbody></table>` : ""}</div>`
      : notEnough("The rules' verdict is recorded next to every new live decision; comparisons appear after the next AI decision.")}`;
  animate(body);
}

/* ---------- Reports ---------- */
const UNIT = { week: "week", month: "month", year: "year" };
function reportState() {
  const q = new URLSearchParams(location.search);
  const period = UNIT[q.get("period")] ? q.get("period") : "week";
  return { period, offset: Math.max(0, parseInt(q.get("offset") || "0", 10) || 0) };
}
function deltaTxt(v, kind, unit) {
  if (v == null || v === 0) return `same as last ${unit}`;
  const up = v > 0, arrow = up ? "▲" : "▼";
  const txt = kind === "pct" ? `${Math.abs(v * 100).toFixed(2)} pt` : kind === "dur" ? dur(Math.abs(v)) : Math.abs(v).toLocaleString();
  return `${arrow} ${txt} vs last ${unit}`;
}
async function renderReports() {
  const el = $("#reports");
  const head = crumbs("Reports");
  const { period, offset } = reportState();
  const link = (p, o) => `/reports?period=${p}${o ? `&offset=${o}` : ""}`;
  el.innerHTML = `${head}<h1>Reports</h1><div class="row report-nav">${seg("Period", [["week", "Week"], ["month", "Month"], ["year", "Year"]], period)}
    <span class="spacer"></span><span id="rep-label" class="mono"></span></div><div id="rep-body">${skeleton(8)}</div>`;
  el.querySelectorAll(".seg [data-v]").forEach((b) => (b.onclick = () => go(link(b.dataset.v, 0))));
  const body = $("#rep-body", el);
  let r;
  try { r = await get(`/api/reports?period=${period}&offset=${offset}`); } catch (e) { body.innerHTML = failCard("the report", e); return; }
  $("#rep-label", el).innerHTML = `${r.has_prev ? `<a class="btn" href="${link(period, offset + 1)}" aria-label="Previous ${period}">‹</a>` : '<span class="btn" aria-disabled="true">‹</span>'}
    <span>${esc(r.label)}${r.partial ? " · so far" : ""}</span>
    ${r.has_next ? `<a class="btn" href="${link(period, offset - 1)}" aria-label="Next ${period}">›</a>` : '<span class="btn" aria-disabled="true">›</span>'}`;
  const s = r.summary, d = r.delta, unit = UNIT[period];
  if (!s.polls && !s.decisions && !s.incidents) {
    body.innerHTML = notEnough(`Nothing recorded for ${r.label}. Reports read the daily rollup, which starts with the first day Nightwarden polled.`);
    return;
  }
  const sev = Object.entries(s.incidents_by_severity).map(([k, v]) => `${v} ${k}`).join(" · ") || "none";
  body.innerHTML = `
    <div class="grid tiles">
      ${tile("Uptime", s.uptime == null ? "—" : count(s.uptime, `rep-up-${period}`, "pct", 2), deltaTxt(d.uptime, "pct", unit))}
      ${tile("Polls", count(s.polls, `rep-polls-${period}`), deltaTxt(d.polls, "n", unit))}
      ${tile("Incidents", count(s.incidents, `rep-inc-${period}`), sev)}
      ${tile("Time to resolve", dur(s.mttr_s), s.resolved ? `${deltaTxt(d.mttr_s, "dur", unit)} · ${s.resolved} resolved` : "nothing resolved")}
      ${tile("Pages", count(s.pages, `rep-pages-${period}`), deltaTxt(d.pages, "n", unit))}
      ${tile("Degraded", s.degraded_pct == null ? "—" : count(s.degraded_pct, `rep-deg-${period}`, "pct", 0), s.decisions ? `of ${s.decisions} decisions` : "no decisions")}
      ${tile("Groq tokens", count(s.tokens, `rep-tok-${period}`), deltaTxt(d.tokens, "n", unit))}
      ${tile("Alert emails", count(s.emails_sent, `rep-mail-${period}`), s.emails_failed ? `${s.emails_failed} failed` : deltaTxt(d.emails_sent, "n", unit))}
    </div>
    <h2>Top signals</h2>
    ${s.top_kinds.length ? `<div class="card">${hbars(s.top_kinds.map((k) => [k.kind.replaceAll("_", " "), k.n]), s.events)}</div>`
      : emptyCard("No signals fired", "Detectors stayed quiet for the whole period.")}
    <h2>Day by day</h2>
    <div class="card"><div class="scroll"><table class="compact"><thead><tr><th>Day</th><th class="num">Uptime</th><th class="num">Polls</th>
      <th class="num">Incidents</th><th class="num">Pages</th><th class="num">Tokens</th></tr></thead><tbody>
      ${r.daily.slice().reverse().map((x) => `<tr><td>${esc(x.date)}</td><td class="num">${x.uptime == null ? "—" : (x.uptime * 100).toFixed(2) + "%"}</td>
        <td class="num">${x.polls}</td><td class="num">${x.incidents_opened}</td><td class="num">${x.pages}</td><td class="num">${num(x.tokens)}</td></tr>`).join("")}
    </tbody></table></div>
    <p class="sub"><a href="/api/reports?period=${period}&offset=${offset}&format=csv" download>Download CSV</a> · compared with ${esc(r.previous.label)}</p></div>`;
  animate(body);
}

/* ---------- About: how it works + evaluation ---------- */
function renderAbout() {
  const el = $("#about");
  if (!el.dataset.done) {
    el.innerHTML = `<div id="about-how"></div><h2 id="evaluation">Evaluation</h2><div id="about-eval"></div>`;
    renderHow($("#about-how", el));
  }
  renderEval($("#about-eval", el), "");
  el.dataset.done = "1";
}

/* ---------- routing: real paths, so every page and incident has a shareable link ---------- */
const PAGES = {
  live: { render: renderLive, nav: "live", title: "Nightwarden" },
  incidents: { nav: "incidents", title: "Incidents · Nightwarden" },
  insights: { render: renderInsights, nav: "insights", title: "Insights · Nightwarden" },
  performance: { render: renderPerformance, nav: "insights", title: "Performance · Nightwarden" },
  reports: { render: renderReports, nav: "insights", title: "Reports · Nightwarden" },
  health: { render: renderHealth, nav: "insights", title: "Health · Nightwarden" },
  usage: { render: renderUsage, nav: "insights", title: "Usage · Nightwarden" },
  eval: { render: () => renderEval(), nav: "insights", title: "Evaluation · Nightwarden" },
  about: { render: renderAbout, nav: "about", title: "About · Nightwarden" },
};
const ALIASES = { "": "live", how: "about" };  // old URLs keep working
function route() {
  if (location.hash.length > 1 && !document.getElementById(location.hash.slice(1))) {  // old #tab/id links keep working
    const h = location.hash.slice(1);
    history.replaceState(null, "", h === "live" ? "/" : "/" + h);
  }
  const [tab, id] = location.pathname.replace(/^\/+|\/+$/g, "").split("/");
  const name = PAGES[tab] ? tab : ALIASES[tab] || "live";
  const page = PAGES[name];
  document.querySelectorAll(".topnav a").forEach((a) => {
    if (a.dataset.tab === page.nav) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  document.querySelectorAll("main>section").forEach((s) => {
    const show = s.id === name;
    if (show && s.hidden) { s.classList.remove("enter"); void s.offsetWidth; s.classList.add("enter"); }
    s.hidden = !show;
  });
  $(".top").classList.remove("open");
  $("#menu").setAttribute("aria-expanded", "false");
  hideTip();
  document.title = page.title;
  if (name === "incidents") renderIncidents(id && decodeURIComponent(id));
  else page.render();
}
function go(path) {
  if (path !== location.pathname + location.search) history.pushState(null, "", path);
  route();
  scrollTo(0, 0);
}
$("#menu").onclick = () => {
  const open = $(".top").classList.toggle("open");
  $("#menu").setAttribute("aria-expanded", String(open));
};
document.addEventListener("click", (e) => {
  const a = e.target.closest?.("a[href]");
  if (!a || a.target || a.hasAttribute("download") || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const u = new URL(a.href, location.href);
  if (u.origin !== location.origin || /^\/(api|static)\//.test(u.pathname)) return;
  e.preventDefault();
  go(u.pathname + u.search);
});
addEventListener("popstate", route);
route();
/* Refresh every 2 minutes, only while the tab is visible: every refresh is a database read. */
const REFRESH_MS = 120000;
let lastRefresh = Date.now();
function refresh() {
  if (document.hidden) return;
  lastRefresh = Date.now();
  if (!$("#live").hidden) renderLive();
  if (!$("#health").hidden) renderHealth();
}
setInterval(refresh, REFRESH_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - lastRefresh >= REFRESH_MS) refresh(); });
