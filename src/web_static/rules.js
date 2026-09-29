"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
  const KINDS = ["plex", "jellyfin", "emby"];
  let cfg = {}, locked = new Set(), found = {}, loaded = false;

  function hdrs() {
    const h = { "X-Requested-With": "jpw", "Content-Type": "application/json" };
    const t = sessionStorage.getItem("jpw-token");
    if (t) h.Authorization = "Bearer " + t;
    return h;
  }
  async function call(path, method, body) {
    const res = await fetch(path, { method, headers: hdrs(), body: body ? JSON.stringify(body) : undefined });
    if (res.status === 401 || res.status === 429) { if (window.jpwLogin) window.jpwLogin(res.status === 429 ? "Too many attempts. Wait a minute and try again." : "Sign in required."); throw new Error("unauthorized"); }
    return { status: res.status, body: await res.json() };
  }
  const serverNames = () => KINDS.flatMap((k) => (cfg[k] || []).map((s) => s.name)).filter(Boolean);
  function list(key) { return (cfg[key] = cfg[key] || []); }
  function prune(key) { if (cfg[key] && !cfg[key].length) delete cfg[key]; }

  function select(options, value, onChange, blank) {
    const s = el("select");
    if (blank) s.append(el("option", { value: "", textContent: blank }));
    options.forEach((o) => s.append(el("option", { value: o, textContent: o, selected: o === value })));
    if (value && !options.includes(value)) s.append(el("option", { value, textContent: value + " (unknown)", selected: true }));
    s.addEventListener("change", () => onChange(s.value));
    return s;
  }
  function text(value, onInput, extra = {}) {
    const i = el("input", { type: "text", value: value || "", autocomplete: "off", ...extra });
    i.addEventListener("input", () => onInput(i.value));
    return i;
  }
  const button = (label, onClick) => { const b = el("button", { type: "button", textContent: label }); b.addEventListener("click", onClick); return b; };
  const labelled = (name, control) => { const l = el("label", {}, name); l.append(control); return l; };

  // datalists keyed per server so suggestions match the chosen server
  function suggestions(kind, server) {
    const id = `dl-${kind}-${server}`.replace(/[^a-zA-Z0-9_-]/g, "_");
    let dl = document.getElementById(id);
    if (!dl) { dl = el("datalist", { id }); document.body.append(dl); }
    const src = found[server] || { users: [], libraries: {} };
    dl.replaceChildren(...(kind === "users" ? src.users : Object.keys(src.libraries)).map((v) => el("option", { value: v })));
    return id;
  }

  function mappingCard(key, aliasKey, aliasLabel, m) {
    const card = el("div", { className: "server" });
    const head = el("header");
    head.append(labelled("Canonical name", text(m.canonical, (v) => { m.canonical = v; })), el("span", { className: "spacer" }));
    if (m.legacy) head.append(el("span", { className: "locked", textContent: "legacy mapping (from .env)" }));
    head.append(button("Remove", () => { cfg[key] = cfg[key].filter((x) => x !== m); prune(key); render(); }));
    card.append(head);
    (m.aliases = m.aliases || []).forEach((a) => {
      const row = el("div", { className: "grid" });
      const names = serverNames();
      const valueInput = text(a[aliasKey], (v) => { a[aliasKey] = v; });
      valueInput.setAttribute("list", suggestions(aliasKey === "username" ? "users" : "libs", a.server));
      row.append(labelled("Server", select(names, a.server, (v) => { a.server = v; render(); }, "choose...")), labelled(aliasLabel, valueInput),
        button("Remove alias", () => { m.aliases = m.aliases.filter((x) => x !== a); render(); }));
      card.append(row);
    });
    card.append(button("Add alias", () => { m.aliases.push({ server: "", [aliasKey]: "" }); render(); }));
    return card;
  }

  function ruleCard(key, itemKey, itemLabel, r) {
    const card = el("div", { className: "server" });
    const grid = el("div", { className: "grid" });
    const names = serverNames();
    grid.append(
      labelled(itemLabel + " (comma-separated, or *)", text((r[itemKey] || []).join(", "), (v) => { r[itemKey] = v.split(",").map((x) => x.trim()).filter(Boolean); })),
      labelled("From", select(names, r.from, (v) => { r.from = v; }, "choose...")),
      labelled("To", select(names, r.to, (v) => { r.to = v; }, "choose...")),
    );
    card.append(grid, button("Remove rule", () => { cfg[key] = cfg[key].filter((x) => x !== r); prune(key); render(); }));
    return card;
  }

  function fill(id, items, emptyText) {
    $(id).replaceChildren(...(items.length ? items : [el("p", { className: "note", textContent: emptyText })]));
  }

  function render() {
    fill("rl-umaps", (cfg.user_mappings || []).map((m) => mappingCard("user_mappings", "username", "Username", m)), "No user mappings.");
    fill("rl-lmaps", (cfg.library_mappings || []).map((m) => mappingCard("library_mappings", "library", "Library name", m)), "No library mappings.");
    fill("rl-urules", (cfg.user_sync_rules || []).map((r) => ruleCard("user_sync_rules", "users", "Users", r)), "No user rules.");
    fill("rl-lrules", (cfg.library_sync_rules || []).map((r) => ruleCard("library_sync_rules", "libraries", "Libraries", r)), "No library rules.");
    const names = serverNames();
    $("ex-from").replaceChildren(...names.map((n) => el("option", { value: n, textContent: n })));
    $("ex-to").replaceChildren(...names.map((n, i) => el("option", { value: n, textContent: n, selected: i === 1 })));
    $("dl-users").replaceChildren(...[...new Set(Object.values(found).flatMap((f) => f.users))].map((v) => el("option", { value: v })));
    $("dl-libs").replaceChildren(...[...new Set(Object.values(found).flatMap((f) => Object.keys(f.libraries)))].map((v) => el("option", { value: v })));
  }

  async function load() {
    const { body } = await call("/api/config", "GET");
    cfg = body.config; locked = new Set(body.locked); loaded = true;
    render();
  }

  function clean(config) {
    const out = JSON.parse(JSON.stringify(config));
    KINDS.forEach((k) => (out[k] || []).forEach((s) => { delete s._new; if (s.sync_to && !s.sync_to.length) delete s.sync_to; }));
    return out;
  }

  async function save() {
    const box = $("rl-errors"), status = $("rl-status");
    status.textContent = "Saving..."; box.hidden = true;
    const { status: code, body } = await call("/api/config", "PUT", clean(cfg));
    if (code === 422) {
      const ul = el("ul"); body.errors.forEach((e) => ul.append(el("li", { textContent: `${e.loc}: ${e.msg}` })));
      box.replaceChildren(el("strong", { textContent: "Not saved. Fix these first:" }), ul); box.hidden = false; status.textContent = "";
      return;
    }
    status.textContent = code === 200 ? "Saved. Applies at the next run." : "Save failed.";
    if (code === 200) await load();
  }

  async function discover() {
    const status = $("rl-discover-status");
    status.textContent = "Contacting servers...";
    const { body } = await call("/api/discovery", "GET");
    found = body.servers;
    const bad = Object.entries(found).filter(([, v]) => !v.ok).map(([n]) => n);
    status.textContent = bad.length ? "Unreachable: " + bad.join(", ") : "Loaded.";
    render();
  }

  async function explain() {
    const out = $("ex-result");
    const from = $("ex-from").value, to = $("ex-to").value;
    const lib = $("ex-lib").value.trim();
    const request = { user: $("ex-user").value.trim(), library: lib, from, to,
      library_type: (found[from] && found[from].libraries[lib]) || "", target_library_type: (found[to] && found[to].libraries[lib]) || "" };
    const { status, body } = await call("/api/explain", "POST", request);
    if (status !== 200) { out.replaceChildren(el("li", { className: "bad", textContent: "Enter a user, library, and two servers." })); return; }
    const rows = body.steps.map((s) => {
      const li = el("li"); li.append(el("strong", { className: s.ok ? "ok" : "bad", textContent: (s.ok ? "✓ " : "✗ ") + s.check }), el("span", { className: "info", textContent: s.detail })); return li;
    });
    const verdict = el("li"); verdict.append(el("strong", { className: body.verdict ? "ok" : "bad", textContent: body.verdict ? "Would sync" : "Would be skipped" }));
    out.replaceChildren(...rows, verdict);
  }

  $("add-umap").addEventListener("click", () => { list("user_mappings").push({ canonical: "", aliases: [{ server: "", username: "" }] }); render(); });
  $("add-lmap").addEventListener("click", () => { list("library_mappings").push({ canonical: "", aliases: [{ server: "", library: "" }] }); render(); });
  $("add-urule").addEventListener("click", () => { list("user_sync_rules").push({ users: [], from: "", to: "" }); render(); });
  $("add-lrule").addEventListener("click", () => { list("library_sync_rules").push({ libraries: [], from: "", to: "" }); render(); });
  $("rl-save").addEventListener("click", save);
  $("rl-discover").addEventListener("click", () => discover().catch(() => { $("rl-discover-status").textContent = "Failed."; }));
  $("ex-run").addEventListener("click", () => explain().catch(() => {}));

  function show(view) {
    ["dash", "settings", "rules"].forEach((v) => { $("view-" + v).hidden = v !== view; $("nav-" + (v === "dash" ? "dash" : v)).classList.toggle("active", v === view); });
    if (view === "rules") load().catch(() => {});
    if (view === "settings" && window.jpwLoadSettings) window.jpwLoadSettings();
  }
  window.jpwShow = show;
  $("nav-rules").addEventListener("click", () => show("rules"));
})();
