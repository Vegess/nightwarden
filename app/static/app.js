const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const pct = (v, d = 1) => v == null ? "—" : (v * 100).toFixed(d) + "%";
const ago = (iso) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 90) return Math.max(0, Math.round(s)) + "s ago";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 129600) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
};
const when = (iso) => iso ? new Date(iso).toLocaleString([], {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "";
async function get(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(path + " → " + r.status);
  return r.json();
}

function spark(values, color, max) {
  const v = values.map((x) => x == null ? null : Number(x));
  const nums = v.filter((x) => x != null);
  if (nums.length < 2) return `<svg class="spark" viewBox="0 0 100 40" preserveAspectRatio="none"><text x="0" y="24" fill="currentColor" font-size="9" opacity=".5">collecting…</text></svg>`;
  const hi = Math.max(max ?? 0, ...nums) || 1;
  const step = 100 / (v.length - 1);
  let d = "", pen = false;
  v.forEach((x, i) => {
    if (x == null) { pen = false; return; }
    const y = 38 - (x / hi) * 34;
    d += (pen ? "L" : "M") + (i * step).toFixed(2) + " " + y.toFixed(2) + " ";
    pen = true;
  });
  return `<svg class="spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

function metricCard(label, value, series, color, max) {
  return `<div class="card"><div class="label">${label}</div><div class="big">${value}</div>${spark(series, color, max)}
  <div class="muted" style="font-size:11px">last 90 min</div></div>`;
}

function decisionBlock(d) {
  const calls = (d.tool_calls || []).map((c) => `<li class="${c.tool === "decide" ? "decide" : ""}">
      <span class="mono">${esc(c.tool)}(${esc(JSON.stringify(c.args || {}))})</span>
      ${c.result_preview ? `<pre class="mono">${esc(c.result_preview)}</pre>`
        : c.result_hidden ? `<div class="muted" style="font-size:12px">result not shown in the public view</div>` : ""}</li>`).join("");
  return `<div class="card" style="margin-top:10px">
    <div class="row"><span class="pill ${esc(d.action)}">${esc(d.action)}</span>
      <span class="pill">${esc(d.confidence)} confidence</span>
      <span class="pill">${d.degraded ? "rules (AI unavailable)" : esc(d.triager)}</span>
      <span class="muted">${when(d.ts)}${d.event ? " · " + esc(d.event.kind) : ""}</span></div>
    <p style="margin:8px 0 0"><strong>${esc(d.summary)}</strong></p>
    ${d.rationale ? `<p class="muted" style="margin:4px 0 0">${esc(d.rationale)}</p>` : ""}
    ${d.event?.detail ? `<p class="mono muted" style="margin:6px 0 0">signal: ${esc(d.event.detail)}</p>` : ""}
    ${calls ? `<details><summary>Investigation: ${d.tool_calls.length} tool call${d.tool_calls.length === 1 ? "" : "s"}, ${d.rounds} round${d.rounds === 1 ? "" : "s"}, ${(d.latency_ms / 1000).toFixed(1)}s</summary><ul class="trace">${calls}</ul></details>` : ""}
  </div>`;
}

async function renderLive() {
  const el = $("#live");
  try {
    const [s, decisions] = await Promise.all([get("/api/status"), get("/api/decisions?limit=8")]);
    $("#svc").textContent = s.service;
    const w = s.window || {};
    const col = (k) => s.series.map((p) => p[k]);
    const state = !s.last_poll ? ["var(--muted)", "Waiting for the first poll"]
      : !s.reachable ? ["var(--bad)", "Unreachable: " + (s.last_error || "no response")]
      : s.open_incidents ? ["var(--warn)", `${s.open_incidents} open incident${s.open_incidents === 1 ? "" : "s"}`]
      : ["var(--ok)", "Healthy"];
    el.innerHTML = `
      <div class="card">
        <div class="row" style="font-size:16px"><span class="dot" style="background:${state[0]}"></span><strong>${esc(state[1])}</strong></div>
        <div class="row muted" style="margin-top:6px">
          <span>watching ${esc(s.target_host || s.service)}</span>·<span>last poll ${ago(s.last_poll)}</span>·
          <span>AI triage: ${esc(s.ai_triage)}${s.last_triage_degraded ? " (last decision used rules)" : ""}</span>
          ${s.commit ? `·<span class="mono">commit ${esc(String(s.commit).slice(0, 7))}</span>` : ""}
          ${s.llm_model ? `·<span>target LLM ${esc(s.llm_model)}</span>` : ""}
        </div>
      </div>
      <h2>Signals</h2>
      <div class="grid">
        ${metricCard("Requests (5 min)", w.requests_5m ?? "—", col("requests_5m"), "var(--info)")}
        ${metricCard("Server error rate", pct(w.error_rate), col("error_rate"), "var(--bad)", 0.1)}
        ${metricCard("Fallback answers", pct(w.fallback_rate), col("fallback_rate"), "var(--warn)", 0.2)}
        ${metricCard("p95 latency", w.latency_p95_ms != null ? Math.round(w.latency_p95_ms) + " ms" : "—", col("latency_p95_ms"), "var(--ok)", 5000)}
      </div>
      <h2>Recent decisions</h2>
      ${decisions.length ? decisions.map(decisionBlock).join("") : `<p class="empty">Nothing has needed a decision yet. Detectors fire on error, latency, fallback, retrieval and traffic anomalies.</p>`}
      ${s.deploys.length ? `<h2>Deploys seen</h2><div class="card scroll"><table><tbody>${s.deploys.map((d) => `<tr><td>${when(d.ts)}</td><td class="mono">${esc(d.commit)}</td><td class="muted">${esc(d.note)}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
  } catch (e) {
    el.innerHTML = `<p class="empty">Couldn't load status (${esc(e.message)}). Retrying…</p>`;
  }
}

async function renderIncidents(openId) {
  const el = $("#incidents");
  try {
    if (openId) {
      const i = await get("/api/incidents/" + encodeURIComponent(openId));
      el.innerHTML = `<p><a href="#incidents">← All incidents</a></p>
        <div class="row"><span class="pill ${esc(i.severity)}">${esc(i.severity)}</span><span class="pill ${esc(i.status)}">${esc(i.status)}</span>
          <span class="mono muted">${esc(i.kind)}</span></div>
        <h1 style="font-size:20px;margin:10px 0 4px">${esc(i.summary)}</h1>
        <p class="muted">Opened ${when(i.opened_at)} · ${i.occurrences} occurrence${i.occurrences === 1 ? "" : "s"}${i.closed_at ? " · closed " + when(i.closed_at) : ""}</p>
        <h2>Timeline</h2>
        <ol class="timeline">
          <li><span class="mono muted">${when(i.opened_at)}</span> opened</li>
          ${i.decisions.map((d) => `<li><span class="mono muted">${when(d.ts)}</span> <span class="pill ${esc(d.action)}">${esc(d.action)}</span> ${esc(d.event?.detail || d.summary)}</li>`).join("")}
          ${i.closed_at ? `<li><span class="mono muted">${when(i.closed_at)}</span> resolved</li>` : ""}
        </ol>
        <h2>Decisions</h2>${i.decisions.map(decisionBlock).join("") || `<p class="empty">No decisions recorded.</p>`}`;
      return;
    }
    const list = await get("/api/incidents?limit=100");
    el.innerHTML = `<h2 style="margin-top:0">Incidents</h2>` + (list.length ? `<div class="list">${list.map((i) => `
      <div data-id="${esc(i.id)}"><div class="row"><span class="pill ${esc(i.severity)}">${esc(i.severity)}</span>
        <span class="pill ${esc(i.status)}">${esc(i.status)}</span><span class="title">${esc(i.summary)}</span></div>
        <div class="muted" style="margin-top:2px">${esc(i.kind)} · opened ${ago(i.opened_at)} · ${i.occurrences}×</div></div>`).join("")}</div>`
      : `<p class="empty">No incidents yet. Only decisions of notify or page open one.</p>`);
    el.querySelectorAll("[data-id]").forEach((n) => n.onclick = () => { location.hash = "incidents/" + n.dataset.id; });
  } catch (e) {
    el.innerHTML = `<p class="empty">Couldn't load incidents (${esc(e.message)}).</p>`;
  }
}

async function renderEval() {
  const el = $("#eval");
  try {
    const r = await get("/api/eval");
    if (!r.available) { el.innerHTML = `<p class="empty">No evaluation results published yet.</p>`; return; }
    const s = r.summary, b = s.baseline || {}, a = s.agent || {};
    const hasAgent = a && a.n;
    const rows = [["accuracy", "Action accuracy"], ["page_precision", "Page precision"], ["page_recall", "Page recall"],
      ["silent_recall", "Silent-degradation recall"], ["loud_accuracy", "Accuracy on loud failures"]];
    const bar = (v, c) => `<div class="bar"><span style="width:${(v ?? 0) * 100}%;background:${c}"></span></div>`;
    el.innerHTML = `
      <h2 style="margin-top:0">Agent vs. frozen rules baseline</h2>
      <p class="muted">${b.n} labelled cases: 12 failure scenarios × 5 seeded variations. Labels and the baseline were frozen before the agent was written.
        ${hasAgent ? `Agent model: <span class="mono">${esc(r.model)}</span>.` : "Agent results not published yet; showing the baseline only."}
        Generated ${when(r.generated_at)}.</p>
      <div class="card scroll"><table>
        <thead><tr><th>Metric</th><th class="num">Rules</th><th style="width:22%"></th>${hasAgent ? `<th class="num">Agent</th><th style="width:22%"></th>` : ""}</tr></thead>
        <tbody>${rows.map(([k, l]) => `<tr><td>${l}</td><td class="num">${pct(b[k])}</td><td>${bar(b[k], "var(--muted)")}</td>
          ${hasAgent ? `<td class="num">${pct(a[k])}</td><td>${bar(a[k], "var(--info)")}</td>` : ""}</tr>`).join("")}</tbody>
      </table></div>
      ${s.agent_cost ? `<div class="grid" style="margin-top:12px">
        <div class="card"><div class="label">Median tool calls</div><div class="big">${s.agent_cost.median_tool_calls}</div></div>
        <div class="card"><div class="label">Mean tokens / event</div><div class="big">${s.agent_cost.mean_tokens}</div></div>
        <div class="card"><div class="label">p95 decision time</div><div class="big">${(s.agent_cost.p95_latency_ms / 1000).toFixed(1)} s</div></div>
        <div class="card"><div class="label">Inconclusive</div><div class="big">${s.agent_cost.inconclusive}</div></div></div>` : ""}
      <h2>Per scenario</h2>
      <div class="card scroll"><table>
        <thead><tr><th>#</th><th>Scenario</th><th>Expected</th><th class="num">Rules</th>${hasAgent ? `<th class="num">Agent</th>` : ""}</tr></thead>
        <tbody>${s.per_scenario.map((p) => `<tr><td>${p.scenario}</td><td>${esc(p.name.replaceAll("_", " "))}${p.silent ? ` <span class="pill">silent</span>` : ""}</td>
          <td><span class="pill ${esc(p.expected)}">${esc(p.expected)}</span></td><td class="num">${p.baseline_correct}/${p.n}</td>
          ${hasAgent ? `<td class="num">${p.agent_correct}/${p.n}</td>` : ""}</tr>`).join("")}</tbody>
      </table></div>
      <p class="muted">"Silent" scenarios are failures that raise no errors: the service keeps answering 200s while quality drops.</p>`;
  } catch (e) {
    el.innerHTML = `<p class="empty">Couldn't load evaluation (${esc(e.message)}).</p>`;
  }
}

function route() {
  const [tab, id] = (location.hash.slice(1) || "live").split("/");
  const name = ["live", "incidents", "eval"].includes(tab) ? tab : "live";
  document.querySelectorAll("nav button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  document.querySelectorAll("main>section").forEach((s) => s.hidden = s.id !== name);
  ({live: renderLive, incidents: () => renderIncidents(id), eval: renderEval})[name]();
}
document.querySelectorAll("nav button").forEach((b) => b.onclick = () => { location.hash = b.dataset.tab; });
addEventListener("hashchange", route);
route();
setInterval(() => { if (!$("#live").hidden && !document.hidden) renderLive(); }, 30000);
