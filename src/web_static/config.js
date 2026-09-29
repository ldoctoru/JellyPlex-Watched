"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const KINDS = ["plex", "jellyfin", "emby"];
  const GENERAL = [
    ["dryrun", "check", "Dry run (log only, change nothing)"],
    ["run_only_once", "check", "Run once and exit"],
    ["generate_guids", "check", "Match by provider GUIDs"],
    ["generate_locations", "check", "Match by file paths"],
    ["debug_level", ["INFO", "DEBUG", "TRACE"], "Log level"],
    ["sleep_duration", "number", "Seconds between runs"],
    ["request_timeout", "number", "Request timeout (s)"],
    ["max_threads", "number", "Max threads"],
  ];
  const DEFAULTS = { dryrun: true, run_only_once: false, generate_guids: true, generate_locations: true, debug_level: "INFO", sleep_duration: 3600, request_timeout: 300, max_threads: 1 };
  const FILTERS = ["whitelist_users", "blacklist_users", "whitelist_libraries", "blacklist_libraries", "whitelist_library_types", "blacklist_library_types"];
  let cfg = {}, locked = new Set(), mask = "********";

  function hdrs() {
    const h = { "X-Requested-With": "jpw", "Content-Type": "application/json" };
    const t = sessionStorage.getItem("jpw-token");
    if (t) h.Authorization = "Bearer " + t;
    return h;
  }
  async function call(path, method, body) {
    const res = await fetch(path, { method, headers: hdrs(), body: body ? JSON.stringify(body) : undefined });
    return { status: res.status, body: await res.json() };
  }
  const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
  function label(text, control) { const l = el("label", {}, text); l.append(control); return l; }
  function lockNote(key) { return locked.has(key) ? el("span", { className: "locked", textContent: "Set by environment variable; edit it there." }) : null; }

  function generalField([key, type, text]) {
    const value = key in cfg ? cfg[key] : DEFAULTS[key];
    let input;
    if (type === "check") {
      input = el("input", { type: "checkbox", checked: !!value });
      input.addEventListener("change", () => { cfg[key] = input.checked; });
      const l = el("label", { className: "check" }); l.append(input, text); input.disabled = locked.has(key);
      const w = el("div"); w.append(l); const n = lockNote(key); if (n) w.append(n); return w;
    }
    if (Array.isArray(type)) {
      input = el("select"); type.forEach((o) => input.append(el("option", { value: o, textContent: o, selected: o === value })));
      input.addEventListener("change", () => { cfg[key] = input.value; });
    } else {
      input = el("input", { type: "number", value: String(value) });
      input.addEventListener("input", () => { cfg[key] = Number(input.value); });
    }
    input.disabled = locked.has(key);
    const w = el("div"); w.append(label(text, input)); const n = lockNote(key); if (n) w.append(n); return w;
  }

  function filterField(key) {
    const area = el("textarea", { value: (cfg[key] || []).join("\n") });
    area.disabled = locked.has(key);
    area.addEventListener("input", () => {
      const list = area.value.split("\n").map((v) => v.trim()).filter(Boolean);
      if (list.length) cfg[key] = list; else delete cfg[key];
    });
    const w = el("div"); w.append(label(key, area)); const n = lockNote(key); if (n) w.append(n); return w;
  }

  function allServers() { return KINDS.flatMap((k) => (cfg[k] || []).map((s) => ({ kind: k, s }))); }

  function textInput(obj, key, text, opts = {}) {
    const input = el("input", { type: opts.secret ? "password" : "text", value: obj[key] || "", autocomplete: "off", disabled: !!opts.disabled });
    input.addEventListener("input", () => { if (input.value) obj[key] = input.value; else delete obj[key]; });
    return label(text, input);
  }

  function serverCard({ kind, s }) {
    const card = el("div", { className: "server" });
    const head = el("header");
    const title = el("h3", { textContent: `${s.name || "(new)"} · ${kind}` });
    const result = el("span", { className: "test-result" });
    const test = el("button", { type: "button", textContent: "Test connection" });
    test.addEventListener("click", async () => {
      result.textContent = "Testing..."; result.className = "test-result";
      try {
        const { body } = await call("/api/config/test-server", "POST", { type: kind, server: s });
        result.textContent = (body.ok ? "Connected · " : "Failed · ") + body.info; result.className = "test-result " + (body.ok ? "ok" : "bad");
      } catch (e) { result.textContent = "Request failed"; result.className = "test-result bad"; }
    });
    const remove = el("button", { type: "button", textContent: "Remove" });
    remove.addEventListener("click", () => {
      cfg[kind] = cfg[kind].filter((x) => x !== s);
      if (!cfg[kind].length) delete cfg[kind];
      allServers().forEach(({ s: o }) => { o.sync_to = (o.sync_to || []).filter((n) => n !== s.name); });
      render();
    });
    head.append(title, result, test, remove);
    const fields = el("div", { className: "grid" });
    const isNew = !!s._new;
    fields.append(textInput(s, "name", "Name (fixed once saved)", { disabled: !isNew }), textInput(s, "baseurl", "Base URL"));
    const tokenLocked = locked.has("token:" + s.name);
    if (kind === "plex") {
      fields.append(textInput(s, "token", tokenLocked ? "Token (set by environment)" : "Token", { secret: true, disabled: tokenLocked }),
        textInput(s, "username", "Username (alternative to token)"), textInput(s, "password", "Password", { secret: true }), textInput(s, "servername", "Plex server name"));
    } else {
      fields.append(textInput(s, "token", tokenLocked ? "API key (set by environment)" : "API key", { secret: true, disabled: tokenLocked }));
    }
    const chips = el("div", { className: "chips" });
    const ssl = el("input", { type: "checkbox", checked: !!s.ssl_bypass });
    if (kind === "plex") { ssl.addEventListener("change", () => { s.ssl_bypass = ssl.checked; }); const l = el("label", { className: "check" }); l.append(ssl, "Bypass SSL verification"); chips.append(l); }
    const push = el("div", { className: "chips" });
    push.append(el("span", { textContent: "Pushes watch state to:" }));
    const others = allServers().filter(({ s: o }) => o !== s && o.name);
    if (!others.length) push.append(el("span", { className: "locked", textContent: "(add another named server)" }));
    others.forEach(({ s: o }) => {
      const cb = el("input", { type: "checkbox", checked: (s.sync_to || []).includes(o.name) });
      cb.addEventListener("change", () => {
        const set = new Set(s.sync_to || []); cb.checked ? set.add(o.name) : set.delete(o.name); s.sync_to = [...set];
      });
      const l = el("label", { className: "check" }); l.append(cb, o.name); push.append(l);
    });
    card.append(head, fields, chips, push);
    return card;
  }

  function render() {
    $("cfg-general").replaceChildren(...GENERAL.map(generalField));
    $("cfg-filters").replaceChildren(...FILTERS.map(filterField));
    const cards = allServers().map(serverCard);
    $("cfg-servers").replaceChildren(...(cards.length ? cards : [el("p", { textContent: "No servers configured." })]));
  }

  async function load() {
    const { body } = await call("/api/config", "GET");
    cfg = body.config; locked = new Set(body.locked); mask = body.mask;
    $("cfg-note").textContent = `Editing ${body.path}. Saving rewrites the file without its comments and keeps a timestamped .bak copy. Mappings and rules are preserved unchanged. Changes apply at the next run; GUI address/port changes need a restart.`;
    render();
  }

  function stripInternal(config) {
    const out = JSON.parse(JSON.stringify(config));
    KINDS.forEach((k) => (out[k] || []).forEach((s) => { delete s._new; if (s.sync_to && !s.sync_to.length) delete s.sync_to; }));
    return out;
  }

  $("add-server").addEventListener("click", () => {
    const kind = $("add-type").value;
    (cfg[kind] = cfg[kind] || []).push({ name: "", baseurl: "", sync_to: [], _new: true });
    render();
  });

  $("cfg-save").addEventListener("click", async () => {
    const box = $("cfg-errors"), status = $("cfg-status");
    status.textContent = "Saving..."; box.hidden = true;
    const { status: code, body } = await call("/api/config", "PUT", stripInternal(cfg));
    if (code === 422) {
      const ul = el("ul"); body.errors.forEach((e) => ul.append(el("li", { textContent: `${e.loc}: ${e.msg}` })));
      box.replaceChildren(el("strong", { textContent: "Not saved. Fix these first:" }), ul); box.hidden = false; status.textContent = "";
      return;
    }
    status.textContent = code === 200 ? "Saved. Applies at the next run." : "Save failed.";
    if (code === 200) await load();
  });

  function show(view) {
    $("view-dash").hidden = view !== "dash"; $("view-settings").hidden = view !== "settings";
    $("nav-dash").classList.toggle("active", view === "dash"); $("nav-settings").classList.toggle("active", view === "settings");
    if (view === "settings") load().catch(() => {});
  }
  $("nav-dash").addEventListener("click", () => show("dash"));
  $("nav-settings").addEventListener("click", () => show("settings"));
})();
