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
function tile(label, value, sub) {
  return `<div class="card"><div class="label">${label}</div><div class="big">${value}</div><div class="sub">${sub || "&nbsp;"}</div></div>`;
}
const emptyCard = (title, body) => `<div class="card empty-card"><strong>${title}</strong><p>${body}</p></div>`;
const failCard = (what, e) => emptyCard(`Couldn't load ${what}`, `${esc(e?.message || e)}. Retrying in a minute.`);
const pathText = (d) => d.decided_by || (d.degraded ? "rules" : "agent");
const pathBadges = (d) => `<span class="pill plain path">decided by ${esc(pathText(d))}</span>${d.degraded ? '<span class="pill degraded">degraded</span>' : ""}`;
const toolCount = (d) => (d.tool_calls || []).filter((c) => c.tool !== "decide").length;
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
  return `<div class="card hero" style="--c:${k.c}">
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
          <span>last poll ${ago(s.last_poll)}</span>
          <span>AI triage: ${esc(s.ai_triage)}${s.last_triage_degraded ? " (last decision used rules)" : ""}</span>
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
  const up = (u) => (u?.pct == null ? "—" : (u.pct * 100).toFixed(u.pct === 1 ? 0 : 2) + "%");
  const polled = (u) => (u?.polls ? `${u.polls} poll${u.polls === 1 ? "" : "s"}` : "no polls in this window");
  return `<div class="grid tiles">
      ${tile("Uptime · 24 h", up(st.uptime_24h), polled(st.uptime_24h))}
      ${tile("Uptime · 7 days", up(st.uptime_7d), polled(st.uptime_7d))}
      ${tile("Incidents · 7 days", st.incidents_7d, st.incidents_7d ? `${st.pages_7d} paged · ${st.open_incidents} open` : "All quiet")}
      ${tile("Time to resolve", dur(st.mttr_s), st.resolved_30d ? `average of ${st.resolved_30d} in 30 days` : "no resolved incidents yet")}
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
  const card = (label, key, big, opts, note) => `<div class="card spark-card"><div class="label">${label}</div><div class="big">${big}</div>
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
  if (!el.innerHTML) el.innerHTML = '<p class="sub">Loading…</p>';
  const [s, st, decisions] = await Promise.allSettled([get("/api/status"), get("/api/stats"), get("/api/decisions?limit=6")]);
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
  el.innerHTML = '<p class="sub">Loading…</p>';
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
async function renderEval() {
  const el = $("#eval");
  if (!el.innerHTML) el.innerHTML = '<p class="sub">Loading…</p>';
  try {
    const r = await get("/api/eval");
    if (!r.available) { el.innerHTML = emptyCard("No evaluation results published yet", "Results appear here after the eval runs (python -m eval.run_eval)."); return; }
    const s = r.summary, b = s.baseline || {}, a = s.agent || {};
    if (!a.n) { el.innerHTML = emptyCard("Only the rules baseline has been scored so far", "The AI agent's numbers appear once an eval run with a model key has finished."); return; }
    const c = s.agent_cost || {};
    el.innerHTML = `
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
    $("#tbl").onclick = () => { evalTable = !evalTable; renderEval(); };
    const svg = $("#scen");
    if (svg) svg.querySelectorAll(".hit").forEach((h) => {
      const p = s.per_scenario[+h.dataset.i];
      const show = (e) => showTip(`<b>${p.scenario}. ${esc(p.name.replaceAll("_", " "))}</b>${p.silent ? " · silent" : ""}<br>expected: ${esc(p.expected)}<br>
        AI agent <b>${p.agent_correct}/${p.n}</b> · rules <b>${p.baseline_correct}/${p.n}</b>`, e.clientX, e.clientY);
      h.addEventListener("pointermove", show); h.addEventListener("pointerleave", hideTip);
    });
  } catch (e) {
    el.innerHTML = failCard("evaluation", e);
  }
}
function scenarioTable(rows) {
  return `<div class="scroll"><table><thead><tr><th>#</th><th>Scenario</th><th>Expected</th><th class="num">AI agent</th><th class="num">Rules</th><th>Agent chose</th></tr></thead><tbody>
    ${rows.map((p) => `<tr><td>${p.scenario}</td><td>${esc(p.name.replaceAll("_", " "))}${p.silent ? ' <span class="pill plain">silent</span>' : ""}</td>
      <td><span class="pill ${esc(p.expected)}">${esc(p.expected)}</span></td><td class="num">${p.agent_correct}/${p.n}</td><td class="num">${p.baseline_correct}/${p.n}</td>
      <td class="sub">${Object.entries(p.agent_actions || {}).map(([k, v]) => `${esc(k)} ×${v}`).join(", ")}</td></tr>`).join("")}</tbody></table></div>`;
}

/* ---------- How it works ---------- */
function renderHow() {
  const el = $("#how");
  if (el.dataset.done) return;
  el.dataset.done = "1";
  const node = (x, y, w, title, sub, cls = "") => `<g class="node ${cls}"><rect x="${x}" y="${y}" width="${w}" height="62" rx="12"/>
    <text x="${x + 14}" y="${y + 26}">${title}</text><text class="s" x="${x + 14}" y="${y + 45}">${sub}</text></g>`;
  const wire = (d, warn) => `<path class="wire" d="${d}"/><path class="flow${warn ? " warn" : ""}" d="${d}"/>`;
  el.innerHTML = `
    <h1>How Nightwarden works</h1>
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

/* ---------- routing: real paths, so every incident has a shareable link ---------- */
const TABS = ["live", "incidents", "eval", "how"];
const TITLES = { live: "Nightwarden", eval: "Evaluation · Nightwarden", how: "How it works · Nightwarden" };
function route() {
  if (location.hash.length > 1) {  // old #tab/id links keep working
    const h = location.hash.slice(1);
    history.replaceState(null, "", h === "live" ? "/" : "/" + h);
  }
  const [tab, id] = location.pathname.replace(/^\/+|\/+$/g, "").split("/");
  const name = TABS.includes(tab) ? tab : "live";
  document.querySelectorAll("nav button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  document.querySelectorAll("main>section").forEach((s) => (s.hidden = s.id !== name));
  hideTip();
  if (TITLES[name]) document.title = TITLES[name];
  ({ live: renderLive, incidents: () => renderIncidents(id && decodeURIComponent(id)), eval: renderEval, how: renderHow })[name]();
}
function go(path) {
  if (path !== location.pathname) history.pushState(null, "", path);
  route();
  scrollTo(0, 0);
}
document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => go(b.dataset.tab === "live" ? "/" : "/" + b.dataset.tab)));
document.addEventListener("click", (e) => {
  const a = e.target.closest?.("a[href]");
  if (!a || a.target || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const u = new URL(a.href, location.href);
  if (u.origin !== location.origin || /^\/(api|static)\//.test(u.pathname)) return;
  e.preventDefault();
  go(u.pathname);
});
addEventListener("popstate", route);
route();
setInterval(() => { if (!$("#live").hidden && !document.hidden) renderLive(); }, 60000);
