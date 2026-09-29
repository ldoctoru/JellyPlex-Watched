"use strict";
const $ = (id) => document.getElementById(id);
let token = sessionStorage.getItem("jpw-token") || "";
let logLast = 0;

async function api(path, method = "GET") {
  const headers = { "X-Requested-With": "jpw" };
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch(path, { method, headers });
  if (res.status === 401) {
    token = prompt("Access token") || "";
    sessionStorage.setItem("jpw-token", token);
    throw new Error("unauthorized");
  }
  return { status: res.status, body: await res.json() };
}

function ago(iso) {
  if (!iso) return "-";
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 90) return Math.round(s) + " s ago";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  return Math.round(s / 3600) + " h ago";
}
function until(iso) {
  if (!iso) return "-";
  const s = Math.max(0, (Date.parse(iso) - Date.now()) / 1000);
  return s < 90 ? "in " + Math.round(s) + " s" : "in " + Math.round(s / 60) + " min";
}
function text(el, value) { el.textContent = value; }

async function refreshStatus() {
  const { body: s } = await api("/api/status");
  const mode = $("mode");
  text(mode, s.dryrun ? "Dry run: ON" : "Dry run: OFF (writes enabled)");
  mode.className = "pill" + (s.dryrun ? " dry" : "");
  text($("t-status"), s.running ? "Running" : s.last_error ? "Last run failed" : "Idle");
  text($("t-status-sub"), s.last_error || "");
  text($("t-last"), ago(s.last_started));
  text($("t-last-sub"), s.last_duration == null ? "" : s.last_duration.toFixed(1) + " s");
  text($("t-next"), s.run_only_once ? "Once" : until(s.next_run));
  text($("t-next-sub"), s.run_only_once ? "run_only_once is set" : "every " + s.sleep_duration + " s");
  text($("t-planned"), String(s.last_planned_servers));
  $("run").disabled = $("preview").disabled = s.running;
}

async function refreshHealth() {
  const list = $("health");
  const { body } = await api("/api/health");
  list.replaceChildren(...body.servers.map((srv) => {
    const li = document.createElement("li");
    const name = document.createElement("span"); name.className = "name"; name.textContent = srv.name;
    const info = document.createElement("span"); info.className = "info"; info.textContent = srv.info + " · " + srv.ms + " ms";
    const st = document.createElement("span"); st.className = srv.ok ? "ok" : "bad"; st.textContent = srv.ok ? "Connected" : "Unreachable";
    li.append(name, info, st);
    return li;
  }));
}

async function refreshPlan() {
  const { body } = await api("/api/plan");
  const meta = $("plan-meta"), tbody = $("plan");
  if (body.running) { text(meta, "building..."); return; }
  if (body.error) { text(meta, "failed: " + body.error); tbody.replaceChildren(); return; }
  if (!body.plan) { text(meta, "no preview yet"); return; }
  text(meta, body.plan.total + " change(s), nothing written" + (body.plan.truncated ? " (showing first " + body.plan.rows.length + ")" : ""));
  tbody.replaceChildren(...body.plan.rows.map((r) => {
    const tr = document.createElement("tr");
    for (const v of [r.title, r.user + " / " + r.library, r.source + " → " + r.destination, r.change]) {
      const td = document.createElement("td"); td.textContent = v; tr.append(td);
    }
    tr.children[2].className = "route";
    return tr;
  }));
}

async function refreshLogs() {
  const { body } = await api("/api/logs?after=" + logLast);
  if (!body.lines.length) return;
  const pre = $("log");
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
  pre.append(body.lines.map((l) => l.line).join("\n") + "\n");
  logLast = body.last;
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}

async function act(path) {
  const { status } = await api(path, "POST");
  const banner = $("banner");
  banner.hidden = status !== 409;
  text(banner, "A run or preview is already in progress.");
  await tick();
}

async function tick() {
  try { await Promise.all([refreshStatus(), refreshPlan(), refreshLogs()]); } catch (e) { /* retry next tick */ }
}

$("run").addEventListener("click", () => act("/api/run"));
$("preview").addEventListener("click", () => act("/api/preview"));
$("check").addEventListener("click", () => refreshHealth().catch(() => {}));
tick();
refreshHealth().catch(() => {});
setInterval(tick, 3000);
