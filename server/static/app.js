/* ==========================================================================
   Theta SPA — vanilla JS, no build step.

   Six views behind one shell: Ask (the agent), Playbooks (saved automations),
   Schedules (automations that run themselves), Activity (the audit trail),
   Connections (accounts, and what each one unlocks) and Settings.

   Two ideas keep this coherent rather than six screens in a trench coat:

   * **The server describes its own capabilities.** `/api/capabilities` returns
     what Theta can do, what state each capability is in and what to try first.
     The home screen, the capability sheets and the Connections page all render
     from that one payload, so the UI cannot claim something the backend does
     not actually offer.
   * **Approval is a decision, not a dialog.** The approval card shows the exact
     action, where it lands, why it stopped and whether it can be undone —
     because "approve this?" with a tool name is not a question anyone can
     answer responsibly.
   ========================================================================== */
(() => {
  "use strict";

  /* ======================================================================= *
   *  ICONS                                                                  *
   * ======================================================================= */
  const ICONS = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    sparkle: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/>',
    layers: '<path d="M12 2 3 7l9 5 9-5-9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    plug: '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    menu: '<path d="M3 12h18M3 6h18M3 18h18"/>',
    moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    mail: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 5L2 7"/>',
    doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/>',
    folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    checkCircle: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
    alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    play: '<path d="m5 3 14 9-14 9V3z"/>',
    pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    pencil: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>',
    arrowUp: '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
    calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  };

  const icon = (name) =>
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;

  // Which glyph stands for each capability, everywhere it appears.
  const CAP_ICON = {
    browser: "globe", gmail: "mail", notion: "doc", files: "folder",
    web: "search", playbooks: "layers", schedules: "clock",
  };

  /* ======================================================================= *
   *  HELPERS                                                                *
   * ======================================================================= */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const inline = (s) =>
    s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
     .replace(/`([^`]+)`/g, "<code>$1</code>")
     .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
              '<a href="$2" target="_blank" rel="noopener">$1</a>');

  function formatText(text) {
    const lines = esc(text).split("\n");
    let html = "", inList = false;
    const close = () => { if (inList) { html += "</ul>"; inList = false; } };
    for (const line of lines) {
      const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
      if (bullet) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += `<li>${inline(bullet[1])}</li>`;
      } else if (line.trim() === "") close();
      else { close(); html += `<p>${inline(line)}</p>`; }
    }
    close();
    return html || "<p></p>";
  }

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { const j = await r.json(); msg = j.message || j.error || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.status === 204 ? {} : r.json();
  }
  const getJSON = (p) => api(p);
  const sendJSON = (p, body, method) =>
    api(p, { method: method || "POST", headers: { "Content-Type": "application/json" },
             body: JSON.stringify(body || {}) });

  async function* streamNDJSON(resp) {
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1);
        if (line.trim()) { try { yield JSON.parse(line); } catch (e) {} }
      }
    }
    if (buf.trim()) { try { yield JSON.parse(buf); } catch (e) {} }
  }

  function toast(message, kind = "") {
    const t = el("div", "toast " + kind);
    t.innerHTML = icon(kind === "err" ? "alert" : "check") + `<span>${esc(message)}</span>`;
    $("#toast").appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, 3400);
  }

  const fmtDate = (iso) => {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleString(undefined,
      { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  };

  /** "in 4h" / "12m ago" — the form that actually answers "is this soon?" */
  function relTime(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const diff = d.getTime() - Date.now();
    const abs = Math.abs(diff);
    const mins = Math.round(abs / 60000);
    let text;
    if (mins < 1) text = "less than a minute";
    else if (mins < 60) text = `${mins} min`;
    else if (mins < 60 * 24) text = `${Math.round(mins / 60)}h`;
    else text = `${Math.round(mins / (60 * 24))}d`;
    return diff >= 0 ? `in ${text}` : `${text} ago`;
  }

  function sheet(node) {
    const box = $("#sheetCard");
    box.innerHTML = "";
    box.appendChild(node);
    $("#sheet").hidden = false;
  }
  function closeSheet() { $("#sheet").hidden = true; }
  $("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeSheet(); document.body.classList.remove("nav-open"); }
  });

  function confirmSheet({ title, body, confirmLabel, danger }) {
    return new Promise((resolve) => {
      const card = el("div");
      card.innerHTML = `<h3>${esc(title)}</h3><div class="desc">${esc(body)}</div>`;
      const actions = el("div", "form-actions");
      const yes = el("button", "btn " + (danger ? "primary" : "primary"), confirmLabel || "Confirm");
      if (danger) { yes.style.background = "var(--danger)"; yes.style.borderColor = "var(--danger)"; yes.style.color = "#fff"; }
      const no = el("button", "btn ghost", "Cancel");
      yes.onclick = () => { closeSheet(); resolve(true); };
      no.onclick = () => { closeSheet(); resolve(false); };
      actions.append(yes, no);
      card.appendChild(actions);
      sheet(card);
      yes.focus();
    });
  }

  function emptyState({ glyph, title, body, actions }) {
    const box = el("div", "empty");
    box.innerHTML =
      `<div class="e-icon">${icon(glyph)}</div>` +
      `<div class="e-title">${esc(title)}</div>` +
      `<div class="e-body">${body}</div>`;
    if (actions && actions.length) {
      const bar = el("div", "e-actions");
      actions.forEach((b) => bar.appendChild(b));
      box.appendChild(bar);
    }
    return box;
  }

  const loadingBox = () => el("div", "loading", '<span class="spinner"></span>');
  const errorBox = (message) => {
    const b = el("div", "banner err");
    b.innerHTML = icon("alert") + `<span>${esc(message)}</span>`;
    return b;
  };

  /* ======================================================================= *
   *  STATE + THEME                                                          *
   * ======================================================================= */
  const state = {
    view: "ask", status: null, caps: null, busy: false,
    hasMessages: false, activityFilter: "all",
  };

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theta_theme", theme);
    $("#themeIcon").innerHTML = icon(theme === "dark" ? "sun" : "moon");
    $("#themeLabel").textContent = theme === "dark" ? "Light" : "Dark";
  }
  function initTheme() {
    const saved = localStorage.getItem("theta_theme");
    applyTheme(saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
    $("#themeToggle").onclick = () =>
      applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
  }

  /* ======================================================================= *
   *  NAVIGATION                                                             *
   * ======================================================================= */
  const TITLES = {
    ask: ["Ask", "Tell Theta what to do and watch it work."],
    playbooks: ["Playbooks", "Runs that worked, saved as automations. These replay without a model."],
    schedules: ["Schedules", "Automations that run themselves, on a timetable."],
    activity: ["Activity", "Every run Theta has made, step by step."],
    connections: ["Connections", "The accounts Theta can act in, and what each one unlocks."],
    settings: ["Settings", "The language model, the browser, and how Theta behaves."],
  };
  const RENDERERS = {
    playbooks: () => renderPlaybooks(),
    schedules: () => renderSchedules(),
    activity: () => renderActivity(),
    connections: () => renderConnections(),
    settings: () => renderSettings(),
  };

  function setView(view) {
    if (!TITLES[view]) view = "ask";
    state.view = view;
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    $$(".view").forEach((v) => v.classList.add("hidden"));
    $("#view-" + view).classList.remove("hidden");
    $("#viewTitle").textContent = TITLES[view][0];
    $("#viewSub").textContent = TITLES[view][1];
    $("#topbarRight").innerHTML = "";
    document.body.classList.remove("nav-open");
    if (RENDERERS[view]) RENDERERS[view]();
  }

  /* ======================================================================= *
   *  LIVE BROWSER RAIL                                                      *
   * ======================================================================= */
  const live = {
    show() { $("#rail").hidden = false; },
    hide() { $("#rail").hidden = true; },
    active(on) { $("#liveDot").className = "live-dot" + (on ? " on" : ""); },
    reset() {
      $("#railShot").innerHTML =
        '<div class="rail-empty">The page appears here as soon as Theta opens one.</div>';
      $("#railUrl").lastElementChild.textContent = "about:blank";
      $("#railStep").textContent = "";
      $("#railNote").textContent = "";
    },
    update(ev) {
      if (ev.url) $("#railUrl").lastElementChild.textContent = ev.url;
      if (ev.index) $("#railStep").textContent = `step ${ev.index}`;
      if (ev.record_id && ev.screenshot) {
        const src = `/api/runs/${encodeURIComponent(ev.record_id)}/shot/${encodeURIComponent(ev.screenshot)}`;
        const box = $("#railShot");
        const img = el("img");
        img.alt = "Live view of the page Theta is on";
        img.src = src;
        img.onload = () => { box.innerHTML = ""; box.appendChild(img); };
      }
      if (ev.summary) $("#railNote").textContent = ev.summary;
    },
  };

  /* ======================================================================= *
   *  ASK — home screen                                                      *
   * ======================================================================= */
  async function loadCapabilities(force) {
    if (state.caps && !force) return state.caps;
    state.caps = await getJSON("/api/capabilities");
    return state.caps;
  }

  async function renderHome() {
    const thread = $("#thread");
    thread.innerHTML = "";
    state.hasMessages = false;
    live.hide();
    live.reset();

    const home = el("div", "home");
    const hero = el("div", "home-hero");
    hero.innerHTML = `
      <div class="mark">θ</div>
      <h2>Theta operates the web for you.</h2>
      <p>Describe a task once. Theta plans it, does it in a real browser and in your
         connected accounts, and asks before anything irreversible — then saves it as
         an automation you can replay or schedule.</p>`;
    home.appendChild(hero);
    thread.appendChild(home);

    let data;
    try { data = await loadCapabilities(); }
    catch (e) { home.appendChild(errorBox(e.message)); return; }

    if (!data.model.ready) home.appendChild(setupCard(data.model));

    // What Theta can do — every chip opens something, none is decoration.
    home.appendChild(sectionHead("What Theta can do",
      "Tap any capability to see what it unlocks"));
    const strip = el("div", "cap-strip");
    data.capabilities.forEach((cap) => strip.appendChild(capChip(cap)));
    home.appendChild(strip);

    // Starter prompts, runnable ones first.
    if (data.starters.length) {
      home.appendChild(sectionHead("Try one of these", "Click to run it"));
      const list = el("div", "starters");
      data.starters.forEach((s) => list.appendChild(starterRow(s, data)));
      home.appendChild(list);
    }
  }

  function sectionHead(title, hint) {
    const h = el("div", "section-head");
    h.innerHTML = `<h3>${esc(title)}</h3><span class="hint">${esc(hint || "")}</span>`;
    return h;
  }

  function setupCard(model) {
    const card = el("div", "card");
    card.style.borderColor = "var(--warn-border)";
    card.innerHTML =
      `<h3>${icon("zap")} One step before we start</h3>
       <div class="desc">Theta needs a language model to work out <em>how</em> to do a task.
         A free Google AI Studio key takes about a minute, or point it at a local Ollama.
         Saved automations replay without one.</div>`;
    const b = el("div", "banner warn");
    b.innerHTML = icon("alert") + `<span>${esc(model.error || "No model configured.")}</span>`;
    card.appendChild(b);
    const go = el("button", "btn primary", "Open Settings");
    go.style.marginTop = "14px";
    go.onclick = () => setView("settings");
    card.appendChild(go);
    return card;
  }

  function capChip(cap) {
    const chip = el("button", "cap-chip" + (cap.state === "connect" ? " is-connect" : ""));
    chip.innerHTML =
      icon(CAP_ICON[cap.key] || "zap") +
      `<span>${esc(cap.name)}</span>` +
      `<span class="state ${esc(cap.state)}"></span>`;
    chip.title = cap.tagline;
    chip.onclick = () => openCapability(cap);
    return chip;
  }

  function starterRow(s, data) {
    const locked = s.state !== "ready";
    const row = el("button", "starter" + (locked ? " locked" : ""));
    row.innerHTML =
      `<span class="ico">${icon(CAP_ICON[s.capability] || "zap")}</span>
       <span class="body">
         <span class="t">${esc(s.title)}</span>
         <span class="p">${esc(s.prompt)}</span>
       </span>` +
      (locked ? `<span class="lock">${esc(s.capability_name)} needed</span>` : "");
    row.onclick = () => {
      if (locked) {
        const cap = data.capabilities.find((c) => c.key === s.capability);
        if (cap) openCapability(cap);
        return;
      }
      $("#composerInput").value = s.prompt;
      sendGoal();
    };
    return row;
  }

  /** The capability sheet: what it is, what it unlocks, and how to get it. */
  function openCapability(cap) {
    const card = el("div");
    const head = el("div", "sheet-head");
    head.innerHTML =
      `<div class="conn-logo">${icon(CAP_ICON[cap.key] || "zap")}</div>
       <div style="flex:1;min-width:0">
         <h3>${esc(cap.name)}</h3>
         <div class="tag" style="font-size:12.5px;color:var(--text-2);margin-top:2px">${esc(cap.tagline)}</div>
       </div>
       ${statePillHTML(cap)}`;
    card.appendChild(head);

    card.appendChild(el("div", "desc", esc(cap.summary)));
    card.appendChild(el("div", "enables-title", "What this lets Theta do"));
    card.appendChild(enablesList(cap.enables));

    if (cap.safety) {
      const s = el("div", "safety");
      s.innerHTML = icon("shield") + `<span>${esc(cap.safety)}</span>`;
      card.appendChild(s);
    }

    if (cap.examples && cap.examples.length) {
      card.appendChild(el("div", "enables-title", "Try it"));
      const list = el("div", "starters");
      cap.examples.forEach((ex) => {
        const row = el("button", "starter");
        row.innerHTML =
          `<span class="ico">${icon("sparkle")}</span>
           <span class="body"><span class="t">${esc(ex.title)}</span>
           <span class="p">${esc(ex.prompt)}</span></span>`;
        row.onclick = () => {
          closeSheet();
          setView("ask");
          $("#composerInput").value = ex.prompt;
          sendGoal();
        };
        list.appendChild(row);
      });
      card.appendChild(list);
    }

    const actions = el("div", "form-actions");
    actions.style.marginTop = "16px";
    const primary = capActionButton(cap, true);
    if (primary) actions.appendChild(primary);
    if (cap.kind === "connection") {
      const manage = el("button", "btn ghost", "Open Connections");
      manage.onclick = () => { closeSheet(); setView("connections"); };
      actions.appendChild(manage);
    } else if (cap.key === "playbooks" || cap.key === "schedules") {
      const open = el("button", "btn ghost", `Open ${cap.name}`);
      open.onclick = () => { closeSheet(); setView(cap.key); };
      actions.appendChild(open);
    }
    const close = el("button", "btn ghost", "Close");
    close.onclick = closeSheet;
    actions.appendChild(close);
    card.appendChild(actions);
    sheet(card);
  }

  function statePillHTML(cap) {
    if (cap.kind !== "connection") {
      return `<span class="state-pill ready">${icon("check")} Built in</span>`;
    }
    if (cap.state === "ready") return `<span class="state-pill ready">${icon("check")} Connected</span>`;
    if (cap.state === "connect") return `<span class="state-pill connect">${icon("plug")} Not connected</span>`;
    return `<span class="state-pill unavailable">${icon("lock")} Unavailable</span>`;
  }

  function enablesList(items) {
    const ul = el("ul", "enables");
    (items || []).forEach((text) => {
      const li = el("li");
      li.innerHTML = icon("check") + `<span>${esc(text)}</span>`;
      ul.appendChild(li);
    });
    return ul;
  }

  /** The one button that moves a capability forward, wherever it is shown. */
  function capActionButton(cap, primary) {
    if (cap.state === "ready" || cap.kind !== "connection") return null;
    if (cap.action === "connect_google") {
      const b = el("button", "btn " + (primary ? "primary" : ""), "Connect Gmail");
      b.innerHTML = icon("plug") + "<span>Connect Gmail</span>";
      b.onclick = () => { location.href = "/api/auth/google/login"; };
      return b;
    }
    if (cap.action === "notion_token") {
      const b = el("button", "btn " + (primary ? "primary" : ""));
      b.innerHTML = icon("plug") + "<span>Connect Notion</span>";
      b.onclick = () => { closeSheet(); setView("connections"); };
      return b;
    }
    return null;
  }

  /* ======================================================================= *
   *  ASK — a turn                                                           *
   * ======================================================================= */
  function addUserMessage(text) {
    const m = el("div", "msg user");
    m.innerHTML = `<div class="who">${icon("user")}<span>You</span></div><div class="bubble"></div>`;
    $(".bubble", m).textContent = text;
    $("#thread").appendChild(m);
    scrollThread();
  }

  function addAgentTurn() {
    const m = el("div", "msg assistant");
    m.innerHTML = `<div class="who"><span style="font-family:Georgia,serif;font-size:13px">θ</span><span>Theta</span></div>
      <div class="bubble">
        <div class="notice-slot"></div>
        <div class="trace"></div>
        <div class="approval-slot"></div>
        <div class="thinking"><span class="d"></span><span class="d"></span><span class="d"></span></div>
        <div class="answer" hidden></div>
        <div class="outputs"></div>
        <div class="offer-slot"></div>
        <div class="dev-slot"></div>
      </div>`;
    $("#thread").appendChild(m);
    scrollThread();

    const traceEl = $(".trace", m), thinkEl = $(".thinking", m), answerEl = $(".answer", m);
    const approvalSlot = $(".approval-slot", m), offerSlot = $(".offer-slot", m);
    const devSlot = $(".dev-slot", m), outEl = $(".outputs", m), noticeSlot = $(".notice-slot", m);
    const steps = {};
    let recordId = "";
    const seenNotices = new Set();

    function note(kind, glyph, text) {
      if (seenNotices.has(text)) return;
      seenNotices.add(text);
      const n = el("div", "note-line " + kind);
      n.innerHTML = icon(glyph) + `<span>${esc(text)}</span>`;
      noticeSlot.appendChild(n);
    }

    return {
      get recordId() { return recordId; },
      started(ev) { recordId = ev.record_id || ""; live.show(); live.active(true); },

      notice(ev) { note("info", "clock", ev.message || ""); scrollThread(); },

      toolStart(ev) {
        const s = el("div", "trace-step running");
        s.innerHTML = `<span class="tnum">${ev.index}</span>
          <span class="tmain"><span class="tlabel"></span> <span class="tsum"></span></span>`;
        $(".tlabel", s).textContent = ev.label || ev.tool;
        traceEl.appendChild(s);
        steps[ev.index] = s;
        live.active(true);
        scrollThread();
      },

      toolEnd(ev) {
        let s = steps[ev.index];
        if (!s) { this.toolStart(ev); s = steps[ev.index]; }
        s.className = "trace-step " + (ev.status || (ev.ok ? "done" : "error"));
        $(".tlabel", s).textContent = ev.label || ev.tool;
        $(".tsum", s).textContent = ev.summary ? "— " + ev.summary : "";
        // A write that was read back and confirmed says so. The unverified case
        // is already spelled out in the summary, so it is not repeated here.
        if (ev.verified === true) {
          const badge = el("span", "verified");
          badge.innerHTML = icon("check") + "<span>verified</span>";
          $(".tmain", s).appendChild(badge);
        }
        if (!recordId && ev.record_id) recordId = ev.record_id;
        live.update(ev);
        (ev.warnings || []).forEach((w) => note("warn", "alert", w));
        scrollThread();
      },

      replay(ev) {
        if (ev.stage === "healing") {
          const n = el("div", "trace-step running");
          n.innerHTML = `<span class="tnum">${icon("refresh")}</span><span class="tmain">
            <span class="tlabel">Re-finding an element</span>
            <span class="theal">the page changed since this was recorded</span></span>`;
          traceEl.appendChild(n);
          scrollThread();
        }
        if (ev.stage === "healed") toast("Playbook repaired and updated");
      },

      approval(ev) {
        thinkEl.hidden = true;
        live.active(false);
        approvalSlot.appendChild(approvalCard(ev, this));
        scrollThread();
      },

      thinking(on) { thinkEl.hidden = !on; },

      final(ev) {
        thinkEl.hidden = true;
        live.active(false);
        if (ev.record_id) recordId = ev.record_id;
        if (ev.answer) { answerEl.hidden = false; answerEl.innerHTML = formatText(ev.answer); }
        if (ev.setup_required) {
          const go = el("button", "btn primary small", "Open Settings");
          go.style.marginTop = "10px";
          go.onclick = () => setView("settings");
          answerEl.appendChild(go);
        }
        (ev.outputs || []).forEach((path) => {
          const a = el("a", "file-pill");
          a.innerHTML = icon("download") + `<span>${esc(path)}</span>`;
          a.href = "/api/files/" + path.split("/").map(encodeURIComponent).join("/");
          a.setAttribute("download", "");
          outEl.appendChild(a);
        });
        if (ev.can_save_playbook && recordId) this.offerPlaybook(recordId);
        if (ev.steps && ev.steps.length) renderDevDetails(devSlot, ev);
        refreshStatus();
        scrollThread();
      },

      offerPlaybook(runId) {
        const box = el("div", "offer");
        box.innerHTML =
          `<span class="ic">${icon("layers")}</span>
           <span class="grow">
             <span class="o-main">That worked — save it as a Playbook?</span>
             <span class="o-sub">Replays the same steps with no model calls, and can then run on a schedule.</span>
           </span>`;
        const btn = el("button", "btn primary small", "Save as Playbook");
        btn.onclick = () => savePlaybookSheet(runId, box);
        box.appendChild(btn);
        offerSlot.appendChild(box);
      },

      error(msg) {
        thinkEl.hidden = true;
        live.active(false);
        answerEl.hidden = false;
        answerEl.innerHTML = "";
        const b = el("div", "banner err");
        b.innerHTML = icon("alert") + `<span>${esc(msg)}</span>`;
        answerEl.appendChild(b);
      },
    };
  }

  /* ---- the approval card ------------------------------------------------ */
  function approvalCard(ev, turn) {
    const d = ev.detail || {};
    const card = el("div", "approval");
    const svc = d.service_label || "";
    card.innerHTML = `
      <div class="head">
        ${icon("shield")}<span>Your approval needed</span>
        ${svc ? `<span class="svc">${esc(svc)}</span>` : ""}
      </div>
      <div class="body">
        <div class="title"></div>
        <div class="facts"></div>
        <div class="edit"></div>
      </div>
      <div class="actions">
        <span class="skip-note"></span>
      </div>`;

    $(".title", card).textContent = d.title || ev.label || "Theta wants to do something";

    const facts = $(".facts", card);
    const fact = (label, value, cls) => {
      if (!value) return;
      const row = el("dl", "fact");
      row.innerHTML = `<dt>${esc(label)}</dt><dd class="${cls || ""}"></dd>`;
      $("dd", row).textContent = value;
      facts.appendChild(row);
    };
    fact("Where", d.target, d.service === "browser" ? "mono" : "");
    fact("Why", d.why || ev.description);
    if (d.consequence) fact("Effect", d.consequence);

    const rev = el("dl", "fact");
    rev.innerHTML =
      `<dt>Undo</dt><dd class="${d.reversible ? "reversible" : "irreversible"}">${
        d.reversible ? "This can be undone." : "This cannot be undone."}</dd>`;
    facts.appendChild(rev);

    // Two things stay editable before you approve them: a research plan, and the
    // wording of an email. Approving something you cannot change first is not
    // much of a decision.
    const editBox = $(".edit", card);
    const field = d.edit_field || "";
    let ta = null;
    if (field === "subquestions" && Array.isArray((ev.args || {}).subquestions)) {
      editBox.appendChild(el("div", "edit-label", "The plan — edit it if you like"));
      ta = el("textarea");
      ta.spellcheck = false;
      ta.value = ev.args.subquestions.join("\n");
      editBox.appendChild(ta);
    } else if (field === "body" && ev.args) {
      editBox.appendChild(el("div", "edit-label", "The message — edit it before it goes"));
      ta = el("textarea");
      ta.rows = 8;
      ta.value = ev.args.body || "";
      editBox.appendChild(ta);
    }

    const actions = $(".actions", card);
    $(".skip-note", actions).textContent = d.on_skip || "Theta carries on without doing this.";
    const go = el("button", "btn success", d.confirm_label || "Approve & continue");
    const no = el("button", "btn danger-ghost", d.decline_label || "Skip this");
    go.onclick = () => {
      let args = null;
      if (field === "subquestions" && ta) {
        args = { ...ev.args,
                 subquestions: ta.value.split("\n").map((s) => s.trim()).filter(Boolean) };
      } else if (field === "body" && ta) {
        args = { ...ev.args, body: ta.value };
      }
      resolveApproval(turn, ev, true, card, args);
    };
    no.onclick = () => resolveApproval(turn, ev, false, card, null);
    actions.insertBefore(no, actions.firstChild);
    actions.insertBefore(go, actions.firstChild);
    return card;
  }

  function renderDevDetails(slot, ev) {
    slot.innerHTML = "";
    const d = el("details", "dev-details");
    d.appendChild(el("summary", "",
      `Full trace · ${ev.steps.length} step(s) · ${esc(ev.llm || "")} · via ${esc(ev.transport || "")}`));
    const pre = el("pre");
    pre.textContent = ev.steps.map((s) =>
      `[${s.status}] ${s.tool} (${s.source})\n  args: ${JSON.stringify(s.args)}\n  → ${s.summary}`
    ).join("\n");
    d.appendChild(pre);
    slot.appendChild(d);
  }

  async function savePlaybookSheet(runId, box) {
    const card = el("div");
    card.innerHTML = `<h3>Save as Playbook</h3>
      <div class="desc">Give it a name you'll recognise. The values you typed become
        inputs you can change on each run — and once it's saved you can put it on a schedule.</div>`;
    const f = el("div", "field");
    f.innerHTML = `<label>Name</label>`;
    const input = el("input", "input");
    input.placeholder = "e.g. Weekly price check";
    f.appendChild(input);
    card.appendChild(f);

    const actions = el("div", "form-actions");
    const save = el("button", "btn primary", "Save Playbook");
    const cancel = el("button", "btn ghost", "Cancel");
    save.onclick = async () => {
      save.disabled = true; save.innerHTML = '<span class="spinner"></span>';
      try {
        const pb = await sendJSON("/api/playbooks", { run_id: runId, name: input.value.trim() });
        closeSheet();
        toast(`Saved “${pb.name}”`, "ok");
        box.innerHTML = "";
        const done = el("span", "grow");
        done.innerHTML = `<span class="o-main">✓ Saved as a Playbook</span>
          <span class="o-sub">Run it any time, or schedule it to run on its own.</span>`;
        const open = el("button", "btn small", "Open Playbooks");
        open.onclick = () => setView("playbooks");
        const sched = el("button", "btn small primary", "Schedule it");
        sched.onclick = () => scheduleSheet(pb);
        box.append(done, sched, open);
        refreshStatus();
      } catch (e) {
        toast(e.message, "err");
        save.disabled = false; save.textContent = "Save Playbook";
      }
    };
    cancel.onclick = closeSheet;
    actions.append(save, cancel);
    card.appendChild(actions);
    sheet(card);
    input.focus();
  }

  async function resolveApproval(turn, ev, approved, card, args) {
    $$("button", card).forEach((b) => (b.disabled = true));
    turn.thinking(true);
    try {
      const resp = await fetch("/api/do/resume", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: ev.run_id, approved, args }),
      });
      if (!resp.ok) throw new Error("That run expired — please start it again.");
      card.remove();
      await consume(resp, turn);
    } catch (e) {
      card.remove();
      turn.error(e.message);
    } finally { setBusy(false); }
  }

  async function consume(resp, turn) {
    for await (const ev of streamNDJSON(resp)) {
      if (ev.type === "run_started") turn.started(ev);
      else if (ev.type === "notice") turn.notice(ev);
      else if (ev.type === "tool_start") turn.toolStart(ev);
      else if (ev.type === "tool_end") turn.toolEnd(ev);
      else if (ev.type === "replay") turn.replay(ev);
      else if (ev.type === "awaiting_approval") turn.approval(ev);
      else if (ev.type === "final") turn.final(ev);
    }
  }

  async function sendGoal() {
    const input = $("#composerInput");
    const text = input.value.trim();
    if (!text || state.busy) return;
    setView("ask");
    if (!state.hasMessages) { $("#thread").innerHTML = ""; state.hasMessages = true; }
    input.value = ""; autoGrow(input);
    live.show(); live.reset();
    addUserMessage(text);
    const turn = addAgentTurn();
    setBusy(true);
    try {
      const resp = await fetch("/api/do", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal: text }),
      });
      await consume(resp, turn);
    } catch (e) { turn.error(e.message); }
    finally { setBusy(false); live.active(false); }
  }

  function setBusy(b) {
    state.busy = b;
    const btn = $("#sendBtn");
    btn.disabled = b;
    btn.innerHTML = b ? '<span class="spinner"></span>' : icon("arrowUp");
  }
  const scrollThread = () => {
    const s = $("#threadScroll");
    s.scrollTop = s.scrollHeight;
  };

  async function newTask() {
    try { await sendJSON("/api/do/clear", {}); } catch (e) {}
    setView("ask");
    await loadCapabilities(true);
    renderHome();
  }

  /* ======================================================================= *
   *  PLAYBOOKS                                                              *
   * ======================================================================= */
  async function renderPlaybooks() {
    const w = $("#playbooksPage");
    w.innerHTML = "";
    const inner = el("div", "page-inner wide");
    w.appendChild(inner);
    inner.appendChild(loadingBox());

    let data;
    try { data = await getJSON("/api/playbooks"); }
    catch (e) { inner.innerHTML = ""; inner.appendChild(errorBox(e.message)); return; }

    inner.innerHTML = "";
    if (!data.playbooks.length) {
      const go = el("button", "btn primary", "Ask Theta to do something");
      go.onclick = () => { setView("ask"); $("#composerInput").focus(); };
      inner.appendChild(emptyState({
        glyph: "layers",
        title: "No playbooks yet",
        body: "Run a task in <b>Ask</b>. When it works, Theta offers to save it here — " +
              "and replaying it needs no model at all, which makes it free and fast.",
        actions: [go],
      }));
      return;
    }

    const grid = el("div", "grid");
    data.playbooks.forEach((pb) => grid.appendChild(playbookTile(pb)));
    inner.appendChild(grid);
  }

  function playbookTile(pb) {
    const card = el("div", "tile");
    card.innerHTML = `
      <div class="t-title"></div>
      <div class="t-goal"></div>
      <div class="t-meta">
        <span>${pb.steps} steps</span>
        <span>·</span>
        <span>${pb.params.length} input${pb.params.length === 1 ? "" : "s"}</span>
        <span>·</span>
        <span>${pb.run_count} run${pb.run_count === 1 ? "" : "s"}</span>
        ${pb.last_status
          ? `<span class="pill ${pb.last_status === "done" ? "ok" : "bad"}">${esc(pb.last_status)}</span>`
          : ""}
      </div>
      <div class="t-actions"></div>`;
    $(".t-title", card).textContent = pb.name;
    $(".t-goal", card).textContent = pb.goal;

    const act = $(".t-actions", card);
    const run = el("button", "btn primary small");
    run.innerHTML = icon("play") + "<span>Run</span>";
    run.onclick = () => runPlaybookSheet(pb);

    const sched = el("button", "btn small");
    sched.innerHTML = icon("clock") + "<span>Schedule</span>";
    sched.onclick = () => scheduleSheet(pb);

    const view = el("button", "btn small ghost", "Steps");
    view.onclick = () => openPlaybook(pb.id);

    const del = el("button", "btn small danger-ghost icon");
    del.innerHTML = icon("trash");
    del.title = "Delete playbook";
    del.onclick = async () => {
      const ok = await confirmSheet({
        title: `Delete “${pb.name}”?`,
        body: "This removes the automation and any schedules that run it. " +
              "The runs it already made stay in Activity.",
        confirmLabel: "Delete", danger: true,
      });
      if (!ok) return;
      try {
        const r = await api("/api/playbooks/" + pb.id, { method: "DELETE" });
        toast(r.schedules_removed
          ? `Playbook and ${r.schedules_removed} schedule(s) deleted`
          : "Playbook deleted", "ok");
        renderPlaybooks(); refreshStatus();
      } catch (e) { toast(e.message, "err"); }
    };
    act.append(run, sched, view, del);
    return card;
  }

  async function openPlaybook(pid) {
    const w = $("#playbooksPage");
    w.innerHTML = "";
    const inner = el("div", "page-inner");
    w.appendChild(inner);
    inner.appendChild(loadingBox());

    let pb;
    try { pb = await getJSON("/api/playbooks/" + pid); }
    catch (e) { inner.innerHTML = ""; inner.appendChild(errorBox(e.message)); return; }

    inner.innerHTML = "";
    const bar = el("div", "detail-bar");
    const back = el("button", "btn small ghost");
    back.innerHTML = icon("back") + "<span>All playbooks</span>";
    back.onclick = renderPlaybooks;
    const spacer = el("div", "spacer");
    const sched = el("button", "btn small");
    sched.innerHTML = icon("clock") + "<span>Schedule</span>";
    sched.onclick = () => scheduleSheet(pb);
    const run = el("button", "btn primary small");
    run.innerHTML = icon("play") + "<span>Run</span>";
    run.onclick = () => runPlaybookSheet(pb);
    bar.append(back, spacer, sched, run);
    inner.appendChild(bar);

    const card = el("div", "card");
    const h = el("h3"); h.textContent = pb.name; card.appendChild(h);
    card.appendChild(el("div", "desc", esc(pb.goal || "")));

    const info = el("div", "banner info");
    info.innerHTML = icon("zap") +
      "<span>This replays <b>without a language model</b>. If a step stops resolving " +
      "because the site changed, Theta re-finds it once and writes the fix back.</span>";
    card.appendChild(info);

    if (pb.params.length) {
      card.appendChild(el("div", "subhead", "Inputs"));
      pb.params.forEach((p) => {
        const row = el("div", "step-row");
        row.innerHTML = `<span class="sn">${icon("pencil")}</span><span class="s-main"></span>`;
        $(".s-main", row).innerHTML =
          `<b>${esc(p.label || p.name)}</b> <span style="color:var(--text-3)">— default “${esc(p.default)}”</span>`;
        card.appendChild(row);
      });
    }

    card.appendChild(el("div", "subhead", "Steps"));
    const list = el("div", "steps-list");
    (pb.step_descriptions || []).forEach((d, i) => {
      const row = el("div", "step-row");
      row.innerHTML = `<span class="sn">${i + 1}</span><span class="s-main"></span>`;
      $(".s-main", row).textContent = d;
      list.appendChild(row);
    });
    card.appendChild(list);
    inner.appendChild(card);
  }

  function runPlaybookSheet(pb) {
    const card = el("div");
    card.innerHTML = `<h3>Run “${esc(pb.name)}”</h3>
      <div class="desc">${pb.params.length
        ? "Adjust the inputs, then run. No model calls needed."
        : "This playbook takes no inputs. It replays exactly as recorded, with no model calls."}</div>`;
    const inputs = {};
    (pb.params || []).forEach((p) => {
      const f = el("div", "field");
      f.innerHTML = `<label>${esc(p.label || p.name)}</label>`;
      const i = el("input", "input");
      i.value = p.default || "";
      f.appendChild(i);
      inputs[p.name] = i;
      card.appendChild(f);
    });
    const actions = el("div", "form-actions");
    const go = el("button", "btn primary");
    go.innerHTML = icon("play") + "<span>Run now</span>";
    const cancel = el("button", "btn ghost", "Cancel");
    go.onclick = () => {
      const values = {};
      Object.entries(inputs).forEach(([k, i]) => (values[k] = i.value));
      closeSheet();
      startReplay(pb.name, `/api/playbooks/${encodeURIComponent(pb.id)}/run`, { values });
    };
    cancel.onclick = closeSheet;
    actions.append(go, cancel);
    card.appendChild(actions);
    sheet(card);
  }

  async function startReplay(label, url, body) {
    setView("ask");
    $("#thread").innerHTML = "";
    state.hasMessages = true;
    live.show(); live.reset();
    addUserMessage(`Run automation: ${label}`);
    const turn = addAgentTurn();
    setBusy(true);
    try {
      const resp = await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      await consume(resp, turn);
    } catch (e) { turn.error(e.message); }
    finally { setBusy(false); live.active(false); refreshStatus(); }
  }

  /* ======================================================================= *
   *  SCHEDULES                                                              *
   * ======================================================================= */
  const CADENCE_LABELS = {
    hourly: "Every hour", daily: "Every day", weekdays: "Weekdays (Mon–Fri)", weekly: "Every week",
  };
  const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  /** Build the schedule sheet for a playbook (create) or an existing schedule (edit). */
  function scheduleSheet(pb, existing) {
    const card = el("div");
    const editing = !!existing;
    card.innerHTML =
      `<h3>${editing ? "Edit schedule" : "Schedule “" + esc(pb.name) + "”"}</h3>
       <div class="desc">Theta will run this automation on its own and record every run in
         Activity. It replays saved steps only — no model, no new decisions.</div>`;

    const nameField = el("div", "field");
    nameField.innerHTML = `<label>Name</label>`;
    const nameInput = el("input", "input");
    nameInput.value = existing ? existing.name : pb.name;
    nameField.appendChild(nameInput);
    card.appendChild(nameField);

    const cadField = el("div", "field");
    cadField.innerHTML = `<label>How often</label>`;
    const cadSel = el("select", "input");
    Object.entries(CADENCE_LABELS).forEach(([value, text]) => {
      const o = el("option", "", text);
      o.value = value;
      if ((existing ? existing.cadence : "daily") === value) o.selected = true;
      cadSel.appendChild(o);
    });
    cadField.appendChild(cadSel);
    card.appendChild(cadField);

    const when = el("div");
    card.appendChild(when);

    let timeInput = null, minuteInput = null, daySel = null;
    function renderWhen() {
      when.innerHTML = "";
      timeInput = minuteInput = daySel = null;
      if (cadSel.value === "hourly") {
        const f = el("div", "field");
        f.innerHTML = `<label>At which minute past the hour</label>`;
        minuteInput = el("input", "input");
        minuteInput.type = "number"; minuteInput.min = "0"; minuteInput.max = "59";
        minuteInput.value = existing ? existing.minute : 0;
        f.appendChild(minuteInput);
        when.appendChild(f);
        return;
      }
      if (cadSel.value === "weekly") {
        const f = el("div", "field");
        f.innerHTML = `<label>Which day</label>`;
        daySel = el("select", "input");
        WEEKDAYS.forEach((name, i) => {
          const o = el("option", "", name);
          o.value = String(i);
          if ((existing ? existing.weekday : 0) === i) o.selected = true;
          daySel.appendChild(o);
        });
        f.appendChild(daySel);
        when.appendChild(f);
      }
      const f = el("div", "field");
      f.innerHTML = `<label>At what time</label>`;
      timeInput = el("input", "input");
      timeInput.type = "time";
      const hh = String(existing ? existing.hour : 9).padStart(2, "0");
      const mm = String(existing ? existing.minute : 0).padStart(2, "0");
      timeInput.value = `${hh}:${mm}`;
      f.appendChild(timeInput);
      f.appendChild(el("div", "help", "Your local time — " +
        Intl.DateTimeFormat().resolvedOptions().timeZone));
      when.appendChild(f);
    }
    cadSel.onchange = renderWhen;
    renderWhen();

    // Inputs the automation takes, pre-filled with whatever it last used.
    const params = (pb && pb.params) || [];
    const inputs = {};
    if (params.length) {
      card.appendChild(el("div", "subhead", "Inputs for each run"));
      params.forEach((p) => {
        const f = el("div", "field");
        f.innerHTML = `<label>${esc(p.label || p.name)}</label>`;
        const i = el("input", "input");
        i.value = (existing && existing.values && existing.values[p.name] != null)
          ? existing.values[p.name] : (p.default || "");
        f.appendChild(i);
        inputs[p.name] = i;
        card.appendChild(f);
      });
    }

    const safety = el("div", "safety");
    safety.innerHTML = icon("shield") +
      "<span>A schedule can never send an email or do anything that needed your approval — " +
      "those actions are not recorded into an automation in the first place.</span>";
    card.appendChild(safety);

    const actions = el("div", "form-actions");
    actions.style.marginTop = "16px";
    const save = el("button", "btn primary", editing ? "Save changes" : "Create schedule");
    const cancel = el("button", "btn ghost", "Cancel");
    save.onclick = async () => {
      const body = {
        playbook_id: pb ? pb.id : existing.playbook_id,
        name: nameInput.value.trim(),
        cadence: cadSel.value,
        tz_offset: new Date().getTimezoneOffset(),
        values: Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value])),
      };
      if (cadSel.value === "hourly") {
        body.minute = parseInt(minuteInput.value, 10) || 0;
      } else {
        const [h, m] = (timeInput.value || "09:00").split(":");
        body.hour = parseInt(h, 10) || 0;
        body.minute = parseInt(m, 10) || 0;
        if (daySel) body.weekday = parseInt(daySel.value, 10) || 0;
      }
      save.disabled = true; save.innerHTML = '<span class="spinner"></span>';
      try {
        if (editing) await sendJSON("/api/schedules/" + existing.id, body, "PATCH");
        else await sendJSON("/api/schedules", body);
        closeSheet();
        toast(editing ? "Schedule updated" : "Schedule created", "ok");
        setView("schedules");
        refreshStatus();
      } catch (e) {
        toast(e.message, "err");
        save.disabled = false; save.textContent = editing ? "Save changes" : "Create schedule";
      }
    };
    cancel.onclick = closeSheet;
    actions.append(save, cancel);
    card.appendChild(actions);
    sheet(card);
  }

  async function renderSchedules() {
    const w = $("#schedulesPage");
    w.innerHTML = "";
    const inner = el("div", "page-inner");
    w.appendChild(inner);
    inner.appendChild(loadingBox());

    let data, pbs;
    try {
      [data, pbs] = await Promise.all([getJSON("/api/schedules"), getJSON("/api/playbooks")]);
    } catch (e) { inner.innerHTML = ""; inner.appendChild(errorBox(e.message)); return; }

    inner.innerHTML = "";

    if (!data.schedules.length) {
      const actions = [];
      if (pbs.playbooks.length) {
        const b = el("button", "btn primary", "Schedule a Playbook");
        b.onclick = () => pickPlaybookToSchedule(pbs.playbooks);
        actions.push(b);
      } else {
        const b = el("button", "btn primary", "Ask Theta to do something");
        b.onclick = () => { setView("ask"); $("#composerInput").focus(); };
        actions.push(b);
      }
      inner.appendChild(emptyState({
        glyph: "clock",
        title: "Nothing scheduled yet",
        body: pbs.playbooks.length
          ? "Put any Playbook on a timetable and Theta runs it on its own — every morning, " +
            "every weekday, every week. Scheduled runs make <b>no model calls</b>, so repeating " +
            "one costs nothing."
          : "Schedules run your saved Playbooks automatically. Ask Theta to do something first, " +
            "save the run as a Playbook, then put it on a timetable.",
        actions,
      }));
      return;
    }

    const blocked = data.schedules.filter((s) => !s.enabled && s.last_status === "blocked");
    if (blocked.length) {
      const b = el("div", "banner warn");
      b.innerHTML = icon("alert") +
        `<span><b>${blocked.length} schedule${blocked.length === 1 ? "" : "s"} paused itself.</b> ` +
        `Theta stopped rather than failing on a timer. Fix what it needs, then resume it below.</span>`;
      b.style.marginBottom = "16px";
      inner.appendChild(b);
    }

    const bar = el("div", "detail-bar");
    const add = el("button", "btn small");
    add.innerHTML = icon("plus") + "<span>New schedule</span>";
    add.onclick = () => pickPlaybookToSchedule(pbs.playbooks);
    bar.append(el("div", "spacer"), add);
    inner.appendChild(bar);

    const rows = el("div", "rows");
    data.schedules.forEach((s) => rows.appendChild(scheduleRow(s, pbs.playbooks)));
    inner.appendChild(rows);
  }

  function scheduleRow(s, playbooks) {
    const row = el("div", "row-item" + (s.enabled ? "" : " paused"));
    const bad = s.last_status === "failed" || s.last_status === "blocked";
    const iconCls = bad ? "bad" : (s.enabled ? "ok" : "");

    const sub = [];
    if (s.playbook_missing) sub.push(`<span class="pill bad">playbook deleted</span>`);
    else sub.push(esc(s.playbook_name || "automation"));
    sub.push("·");
    sub.push(esc(s.cadence_label));
    if (s.enabled && s.next_run) {
      sub.push("·");
      sub.push(`next ${esc(relTime(s.next_run))}`);
    } else if (!s.enabled) {
      sub.push(`<span class="pill">paused</span>`);
    }
    if (s.last_status) {
      const cls = s.last_status === "done" ? "ok"
        : s.last_status === "skipped" ? "" : s.last_status === "blocked" ? "warn" : "bad";
      sub.push(`<span class="pill ${cls}">last: ${esc(s.last_status)}</span>`);
    }

    row.innerHTML = `
      <div class="r-icon ${iconCls}">${icon(bad ? "alert" : "clock")}</div>
      <div class="r-body">
        <div class="r-title"></div>
        <div class="r-sub">${sub.join(" ")}</div>
      </div>
      <div class="r-actions"></div>`;
    $(".r-title", row).textContent = s.name;

    if (s.last_error) {
      const note = el("div", "note-line warn");
      note.style.margin = "8px 0 0";
      note.innerHTML = icon("info") + `<span>${esc(s.last_error)}</span>`;
      $(".r-body", row).appendChild(note);
    }

    const act = $(".r-actions", row);

    const toggle = el("button", "btn small icon");
    toggle.innerHTML = icon(s.enabled ? "pause" : "play");
    toggle.title = s.enabled ? "Pause this schedule" : "Resume this schedule";
    toggle.onclick = async () => {
      toggle.disabled = true;
      try {
        await sendJSON("/api/schedules/" + s.id, { enabled: !s.enabled }, "PATCH");
        toast(s.enabled ? "Schedule paused" : "Schedule resumed", "ok");
        renderSchedules(); refreshStatus();
      } catch (e) { toast(e.message, "err"); toggle.disabled = false; }
    };

    const runNow = el("button", "btn small");
    runNow.innerHTML = icon("play") + "<span>Run now</span>";
    runNow.title = "Run it once, right now, so you can watch what it does";
    runNow.disabled = !!s.playbook_missing;
    runNow.onclick = () =>
      startReplay(s.name, `/api/schedules/${encodeURIComponent(s.id)}/run`, {});

    const edit = el("button", "btn small icon");
    edit.innerHTML = icon("pencil");
    edit.title = "Edit schedule";
    edit.disabled = !!s.playbook_missing;
    edit.onclick = () => {
      const pb = playbooks.find((p) => p.id === s.playbook_id);
      if (!pb) { toast("That playbook no longer exists", "err"); return; }
      scheduleSheet(pb, s);
    };

    const del = el("button", "btn small danger-ghost icon");
    del.innerHTML = icon("trash");
    del.title = "Delete schedule";
    del.onclick = async () => {
      const ok = await confirmSheet({
        title: `Delete “${s.name}”?`,
        body: "The Playbook itself is kept — only the timetable is removed.",
        confirmLabel: "Delete", danger: true,
      });
      if (!ok) return;
      try {
        await api("/api/schedules/" + s.id, { method: "DELETE" });
        toast("Schedule deleted", "ok");
        renderSchedules(); refreshStatus();
      } catch (e) { toast(e.message, "err"); }
    };

    act.append(toggle, runNow, edit, del);
    return row;
  }

  function pickPlaybookToSchedule(playbooks) {
    if (!playbooks.length) {
      toast("Save a Playbook first — schedules run those", "err");
      return;
    }
    const card = el("div");
    card.innerHTML = `<h3>Which automation?</h3>
      <div class="desc">Schedules run saved Playbooks. Pick the one to put on a timetable.</div>`;
    const list = el("div", "starters");
    playbooks.forEach((pb) => {
      const row = el("button", "starter");
      row.innerHTML =
        `<span class="ico">${icon("layers")}</span>
         <span class="body"><span class="t"></span><span class="p"></span></span>`;
      $(".t", row).textContent = pb.name;
      $(".p", row).textContent = `${pb.steps} steps · ${pb.run_count} run(s)`;
      row.onclick = () => { closeSheet(); scheduleSheet(pb); };
      list.appendChild(row);
    });
    card.appendChild(list);
    const actions = el("div", "form-actions");
    const cancel = el("button", "btn ghost", "Cancel");
    cancel.onclick = closeSheet;
    actions.appendChild(cancel);
    card.appendChild(actions);
    sheet(card);
  }

  /* ======================================================================= *
   *  ACTIVITY                                                               *
   * ======================================================================= */
  const TRIGGER_META = {
    task: { label: "Task", glyph: "sparkle" },
    playbook: { label: "Playbook", glyph: "layers" },
    schedule: { label: "Scheduled", glyph: "clock" },
  };
  const FILTERS = [
    ["all", "All"], ["task", "Tasks"], ["playbook", "Playbooks"],
    ["schedule", "Scheduled"], ["failed", "Failed"],
  ];

  async function renderActivity() {
    const w = $("#activityPage");
    w.innerHTML = "";
    const inner = el("div", "page-inner");
    w.appendChild(inner);
    inner.appendChild(loadingBox());

    let data;
    try { data = await getJSON("/api/runs?limit=100"); }
    catch (e) { inner.innerHTML = ""; inner.appendChild(errorBox(e.message)); return; }

    inner.innerHTML = "";
    if (!data.runs.length) {
      const go = el("button", "btn primary", "Ask Theta to do something");
      go.onclick = () => { setView("ask"); $("#composerInput").focus(); };
      inner.appendChild(emptyState({
        glyph: "activity",
        title: "No runs yet",
        body: "Everything Theta does is recorded here — every step, every screenshot, " +
              "every approval — whether you started it or a schedule did.",
        actions: [go],
      }));
      return;
    }

    const bar = el("div", "filters");
    FILTERS.forEach(([key, label]) => {
      const b = el("button", "filter" + (state.activityFilter === key ? " active" : ""), label);
      b.onclick = () => { state.activityFilter = key; renderActivity(); };
      bar.appendChild(b);
    });
    inner.appendChild(bar);

    const runs = data.runs.filter((r) =>
      state.activityFilter === "all" ? true
      : state.activityFilter === "failed" ? r.status === "failed"
      : (r.trigger || "task") === state.activityFilter);

    if (!runs.length) {
      inner.appendChild(emptyState({
        glyph: "search", title: "Nothing here",
        body: "No runs match this filter yet.",
      }));
      return;
    }

    const rows = el("div", "rows");
    runs.forEach((r) => rows.appendChild(activityRow(r)));
    inner.appendChild(rows);
  }

  function activityRow(r) {
    const meta = TRIGGER_META[r.trigger || "task"] || TRIGGER_META.task;
    const ok = r.status === "done";
    const row = el("div", "row-item clickable");
    row.innerHTML = `
      <div class="r-icon ${ok ? "ok" : r.status === "failed" ? "bad" : ""}">${icon(meta.glyph)}</div>
      <div class="r-body">
        <div class="r-title"></div>
        <div class="r-sub">
          <span class="pill">${esc(meta.label)}</span>
          <span>${esc(fmtDate(r.created))}</span>
          <span>·</span><span>${r.steps} steps</span>
          <span>·</span><span>${r.seconds}s</span>
          ${r.approvals ? `<span class="pill accent">${r.approvals} approval${r.approvals === 1 ? "" : "s"}</span>` : ""}
          <span class="pill ${ok ? "ok" : r.status === "failed" ? "bad" : ""}">${esc(r.status)}</span>
        </div>
      </div>
      <div class="r-actions">${icon("back")}</div>`;
    $(".r-title", row).textContent = r.goal;
    $(".r-actions", row).style.transform = "rotate(180deg)";
    $(".r-actions", row).style.color = "var(--text-3)";
    row.onclick = () => openRun(r.id);
    return row;
  }

  async function openRun(runId) {
    const w = $("#activityPage");
    w.innerHTML = "";
    const inner = el("div", "page-inner");
    w.appendChild(inner);
    inner.appendChild(loadingBox());

    let r;
    try { r = await getJSON("/api/runs/" + runId); }
    catch (e) { inner.innerHTML = ""; inner.appendChild(errorBox(e.message)); return; }

    inner.innerHTML = "";
    const bar = el("div", "detail-bar");
    const back = el("button", "btn small ghost");
    back.innerHTML = icon("back") + "<span>All activity</span>";
    back.onclick = renderActivity;
    const del = el("button", "btn small danger-ghost");
    del.innerHTML = icon("trash") + "<span>Delete run</span>";
    del.onclick = async () => {
      const ok = await confirmSheet({
        title: "Delete this run?",
        body: "The trace and its screenshots are removed permanently.",
        confirmLabel: "Delete", danger: true,
      });
      if (!ok) return;
      try {
        await api("/api/runs/" + runId, { method: "DELETE" });
        toast("Run deleted", "ok"); renderActivity(); refreshStatus();
      } catch (e) { toast(e.message, "err"); }
    };
    bar.append(back, el("div", "spacer"), del);
    inner.appendChild(bar);

    const meta = TRIGGER_META[r.trigger || "task"] || TRIGGER_META.task;
    const card = el("div", "card");
    const h = el("h3");
    h.textContent = r.goal;
    card.appendChild(h);
    card.appendChild(el("div", "desc",
      `<span class="pill">${esc(meta.label)}</span> ` +
      `${esc(fmtDate(r.created))} · ${r.steps.length} steps · ${r.seconds}s · ` +
      `<b>${esc(r.status)}</b>${r.model ? " · " + esc(r.model) : ""}`));

    const shots = r.steps.filter((s) => s.screenshot);
    if (shots.length) {
      const strip = el("div", "shot-strip");
      shots.forEach((s) => {
        const img = el("img");
        img.src = `/api/runs/${encodeURIComponent(r.id)}/shot/${encodeURIComponent(s.screenshot)}`;
        img.alt = `Step ${s.index}`;
        img.loading = "lazy";
        img.onclick = () => {
          const big = el("div");
          big.innerHTML = `<h3>Step ${s.index} — ${esc(s.label || s.tool)}</h3>
            <div class="desc">${esc(s.url || "")}</div>`;
          const full = el("img"); full.src = img.src;
          big.appendChild(full);
          sheet(big);
        };
        strip.appendChild(img);
      });
      card.appendChild(strip);
    }

    card.appendChild(el("div", "subhead", "Steps"));
    const list = el("div", "steps-list");
    r.steps.forEach((s) => {
      const row = el("div", "step-row " + (s.ok ? (s.healed ? "healed" : "") : "error"));
      row.innerHTML = `<span class="sn">${s.index}</span><span class="s-main"></span>`;
      $(".s-main", row).innerHTML =
        `<b>${esc(s.label || s.tool)}</b> <span style="color:var(--text-2)">${esc(s.summary || "")}</span>` +
        (s.healed ? ` <span class="pill warn">re-found</span>` : "");
      list.appendChild(row);
    });
    card.appendChild(list);

    if (r.answer) {
      card.appendChild(el("div", "subhead", "Result"));
      const ans = el("div", "answer");
      ans.innerHTML = formatText(r.answer);
      card.appendChild(ans);
    }
    if ((r.outputs || []).length) {
      const outs = el("div", "outputs");
      r.outputs.forEach((path) => {
        const a = el("a", "file-pill");
        a.innerHTML = icon("download") + `<span>${esc(path)}</span>`;
        a.href = "/api/files/" + path.split("/").map(encodeURIComponent).join("/");
        a.setAttribute("download", "");
        outs.appendChild(a);
      });
      card.appendChild(outs);
    }
    inner.appendChild(card);
  }

  /* ======================================================================= *
   *  CONNECTIONS                                                            *
   * ======================================================================= */
  async function renderConnections() {
    const w = $("#connectionsPage");
    w.innerHTML = "";
    const inner = el("div", "page-inner");
    w.appendChild(inner);
    inner.appendChild(loadingBox());

    let data;
    try { data = await loadCapabilities(true); }
    catch (e) { inner.innerHTML = ""; inner.appendChild(errorBox(e.message)); return; }

    inner.innerHTML = "";
    const intro = el("div", "banner accent");
    intro.innerHTML = icon("info") +
      "<span>Connected accounts let Theta work through a service's own API instead of " +
      "driving its website — faster, and it works headless, because Theta never types a password.</span>";
    intro.style.marginBottom = "18px";
    inner.appendChild(intro);

    data.capabilities.filter((c) => c.kind === "connection")
      .forEach((cap) => inner.appendChild(connectionCard(cap)));

    inner.appendChild(el("div", "subhead", "Always available"));
    const note = el("div", "desc");
    note.style.cssText = "font-size:13px;color:var(--text-2);margin:-4px 0 12px";
    note.textContent = "These ship with Theta and need no setup.";
    inner.appendChild(note);

    const grid = el("div", "grid");
    data.capabilities.filter((c) => c.kind !== "connection").forEach((cap) => {
      const tile = el("div", "tile");
      tile.style.cursor = "pointer";
      tile.innerHTML =
        `<div class="t-title" style="display:flex;align-items:center;gap:9px">
           <span style="color:var(--text-2);display:inline-flex">${icon(CAP_ICON[cap.key] || "zap")}</span>
           ${esc(cap.name)}
         </div>
         <div class="t-goal">${esc(cap.tagline)}</div>
         <div class="t-meta">
           ${cap.count != null ? `<span>${cap.count} saved</span>` : ""}
           <span class="pill ok">${icon("check")} ready</span>
         </div>`;
      tile.onclick = () => openCapability(cap);
      grid.appendChild(tile);
    });
    inner.appendChild(grid);
  }

  function connectionCard(cap) {
    const card = el("div", "conn-card" + (cap.state === "ready" ? " is-ready" : ""));

    const head = el("div", "conn-head");
    head.innerHTML =
      `<div class="conn-logo">${icon(CAP_ICON[cap.key] || "plug")}</div>
       <div class="conn-titles">
         <h3>${esc(cap.name)} ${statePillHTML(cap)}</h3>
         <div class="tag">${esc(cap.account ? `Connected as ${cap.account}` : cap.tagline)}</div>
       </div>
       <div class="conn-actions"></div>`;
    card.appendChild(head);

    const body = el("div", "conn-body");
    body.appendChild(el("div", "desc", esc(cap.summary)));
    body.appendChild(el("div", "enables-title", "What this lets Theta do"));
    body.appendChild(enablesList(cap.enables));

    // State-specific setup. Everything a user must do lives here, not in a
    // generic Settings form.
    if (cap.state === "unavailable") {
      const b = el("div", "banner warn");
      b.innerHTML = icon("lock") + `<span>${esc(cap.setup)}</span>`;
      body.appendChild(b);
    } else if (cap.state === "connect" && cap.key === "notion") {
      body.appendChild(notionConnectForm(cap));
    } else if (cap.state === "connect") {
      const b = el("div", "banner info");
      b.innerHTML = icon("info") + `<span>${esc(cap.setup)}</span>`;
      body.appendChild(b);
    }

    if (cap.safety) {
      const s = el("div", "safety");
      s.style.marginTop = "12px";
      s.innerHTML = icon("shield") + `<span>${esc(cap.safety)}</span>`;
      body.appendChild(s);
    }

    if (cap.tools && cap.tools.length) {
      const d = el("details", "dev-details");
      d.appendChild(el("summary", "", `Under the hood — ${cap.tools.length} tools`));
      const names = el("div", "tool-names");
      cap.tools.forEach((t) => names.appendChild(el("span", "tool-name", esc(t))));
      d.appendChild(names);
      body.appendChild(d);
    }
    card.appendChild(body);

    // Head actions
    const actions = $(".conn-actions", head);
    if (cap.state === "ready") {
      const off = el("button", "btn small danger-ghost", "Disconnect");
      off.onclick = async () => {
        const ok = await confirmSheet({
          title: `Disconnect ${cap.name}?`,
          body: `Theta will lose access immediately. Any schedule that needs ${cap.name} ` +
                "will pause itself rather than fail.",
          confirmLabel: "Disconnect", danger: true,
        });
        if (!ok) return;
        try {
          await sendJSON(cap.key === "gmail"
            ? "/api/auth/google/disconnect" : "/api/connections/notion/disconnect", {});
          toast(`${cap.name} disconnected`, "ok");
          renderConnections(); refreshStatus();
        } catch (e) { toast(e.message, "err"); }
      };
      actions.appendChild(off);
    } else {
      const b = capActionButton(cap, true);
      if (b && cap.key !== "notion") actions.appendChild(b);
    }
    return card;
  }

  /** Notion connects with a pasted integration secret, so the form lives inline. */
  function notionConnectForm(cap) {
    const box = el("div");
    const f = el("div", "field");
    f.innerHTML = `<label>Integration token</label>`;
    const input = el("input", "input");
    input.type = "password";
    input.dataset.field = "notion_token";
    input.placeholder = "ntn_…";
    f.appendChild(input);
    f.appendChild(el("div", "help", cap.setup));
    box.appendChild(f);

    const actions = el("div", "form-actions");
    const save = el("button", "btn primary", "Connect Notion");
    const test = el("button", "btn", "Test token");
    const result = el("div", "test-result");
    save.onclick = async () => {
      if (!input.value.trim()) { toast("Paste your integration secret first", "err"); return; }
      save.disabled = true; save.innerHTML = '<span class="spinner"></span>';
      try {
        await sendJSON("/api/settings", { notion_token: input.value.trim() });
        toast("Notion connected", "ok");
        renderConnections(); refreshStatus();
      } catch (e) {
        toast(e.message, "err");
        save.disabled = false; save.textContent = "Connect Notion";
      }
    };
    test.onclick = async () => {
      test.disabled = true; test.innerHTML = '<span class="spinner"></span> Testing';
      result.className = "test-result"; result.textContent = "";
      try {
        const r = await sendJSON("/api/settings/test-notion",
                                 { notion_token: input.value.trim() });
        result.className = "test-result " + (r.ok ? "ok" : "err");
        result.textContent = (r.ok ? "✓ " : "⚠ ") + r.message;
      } catch (e) { result.className = "test-result err"; result.textContent = "⚠ " + e.message; }
      test.disabled = false; test.textContent = "Test token";
    };
    actions.append(save, test, result);
    box.appendChild(actions);
    return box;
  }

  /* ======================================================================= *
   *  SETTINGS                                                               *
   * ======================================================================= */
  const PROVIDER_LABELS = {
    gemini: "Google Gemini", ollama: "Ollama (local)", openai: "OpenAI-compatible",
  };
  const SEARCH_LABELS = {
    duckduckgo: "DuckDuckGo (no key needed)", tavily: "Tavily", brave: "Brave Search",
  };

  async function renderSettings() {
    const w = $("#settingsPage");
    w.innerHTML = "";
    const inner = el("div", "page-inner");
    w.appendChild(inner);
    inner.appendChild(loadingBox());

    let s;
    try { s = await getJSON("/api/settings"); }
    catch (e) { inner.innerHTML = ""; inner.appendChild(errorBox(e.message)); return; }

    inner.innerHTML = "";
    inner.appendChild(modelCard(s));
    inner.appendChild(browserCard(s));
    inner.appendChild(searchCard(s));

    // Connections live on their own page; this is the signpost to it.
    const pointer = el("div", "card");
    pointer.innerHTML =
      `<h3>${icon("plug")} Connected accounts</h3>
       <div class="desc">Gmail and Notion are managed on their own page, alongside what
         each one lets Theta do.</div>`;
    const go = el("button", "btn", "Open Connections");
    go.onclick = () => setView("connections");
    pointer.appendChild(go);
    inner.appendChild(pointer);
  }

  function field(label, name, value, placeholder, type) {
    const f = el("div", "field");
    f.innerHTML = `<label>${esc(label)}</label>`;
    const inp = el("input", "input");
    inp.dataset.field = name;
    inp.value = value == null ? "" : value;
    inp.placeholder = placeholder || "";
    if (type) inp.type = type;
    f.appendChild(inp);
    return f;
  }

  function collect(root) {
    const data = {};
    $$("[data-field]", root).forEach((inp) => {
      const key = inp.dataset.field;
      if (inp.type === "checkbox") data[key] = inp.checked;
      // Credentials: blank means "leave whatever is stored alone", so re-saving
      // a form never silently wipes a key. Clearing one is always explicit.
      else if (key.includes("key") || key.includes("token")) {
        if (inp.value.trim()) data[key] = inp.value.trim();
      } else data[key] = inp.value.trim();
    });
    return data;
  }

  function wireSaveTest(card, { savePath, testPath, testLabel, after }) {
    const result = el("div", "test-result");
    const actions = el("div", "form-actions");
    const saveBtn = el("button", "btn primary", "Save");
    actions.appendChild(saveBtn);
    let testBtn = null;
    if (testPath) {
      testBtn = el("button", "btn", testLabel || "Test connection");
      actions.appendChild(testBtn);
    }
    actions.appendChild(result);
    card.appendChild(actions);

    saveBtn.onclick = async () => {
      saveBtn.disabled = true; saveBtn.innerHTML = '<span class="spinner"></span>';
      try {
        await sendJSON(savePath, collect(card));
        toast("Saved", "ok");
        state.caps = null;
        await refreshStatus();
        (after || renderSettings)();
      } catch (e) {
        toast(e.message, "err");
        saveBtn.disabled = false; saveBtn.textContent = "Save";
      }
    };
    if (testBtn) {
      testBtn.onclick = async () => {
        testBtn.disabled = true; testBtn.innerHTML = '<span class="spinner"></span> Testing';
        result.className = "test-result"; result.textContent = "";
        try {
          const r = await sendJSON(testPath, collect(card));
          result.className = "test-result " + (r.ok ? "ok" : "err");
          result.textContent = (r.ok ? "✓ " : "⚠ ") + r.message;
        } catch (e) { result.className = "test-result err"; result.textContent = "⚠ " + e.message; }
        testBtn.disabled = false; testBtn.textContent = testLabel || "Test connection";
      };
    }
  }

  function modelCard(s) {
    const card = el("div", "card");
    card.innerHTML =
      `<h3>${icon("zap")} Language model</h3>
       <div class="desc">Active: <strong>${esc(s.active_label)}</strong>. This is what works
         out <em>how</em> to do a task. Playbooks and schedules replay without it.</div>`;
    if (!s.model_ready) {
      const b = el("div", "banner warn");
      b.innerHTML = icon("alert") +
        `<span>${esc(s.model_error || "No model configured.")} Theta can't plan a task until this is set.</span>`;
      b.style.marginBottom = "16px";
      card.appendChild(b);
    }
    const provField = el("div", "field");
    provField.innerHTML = `<label>Provider</label>`;
    const sel = el("select", "input");
    sel.dataset.field = "provider";
    s.providers.forEach((p) => {
      const o = el("option", "", PROVIDER_LABELS[p] || p);
      o.value = p; if (p === s.provider) o.selected = true;
      sel.appendChild(o);
    });
    provField.appendChild(sel);
    card.appendChild(provField);
    const dynamic = el("div");
    card.appendChild(dynamic);

    function keyField(help) {
      const f = el("div", "field");
      f.innerHTML = `<label>API key</label>`;
      const inp = el("input", "input");
      inp.type = "password"; inp.dataset.field = "api_key";
      inp.placeholder = s.has_api_key ? `${s.api_key_masked} (from ${s.api_key_source})` : "Paste your key";
      f.appendChild(inp);
      f.appendChild(el("div", "help", s.has_api_key
        ? "A key is set. Leave blank to keep it. Keys are encrypted at rest and never sent to the browser."
        : help));
      return f;
    }
    function renderDynamic() {
      dynamic.innerHTML = "";
      const p = sel.value;
      if (p === "gemini") {
        dynamic.appendChild(field("Model", "gemini_model", s.gemini_model, "gemini-flash-latest"));
        dynamic.appendChild(keyField(
          'Free key at <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener">aistudio.google.com</a> — no billing required.'));
      } else if (p === "ollama") {
        dynamic.appendChild(field("Host", "ollama_host", s.ollama_host, "http://localhost:11434"));
        dynamic.appendChild(field("Model", "ollama_model", s.ollama_model, "llama3.1"));
        const b = el("div", "banner info");
        b.innerHTML = icon("info") +
          "<span>Fully offline. Browser operation asks a lot of a model — 8B+ is the realistic floor.</span>";
        dynamic.appendChild(b);
      } else {
        dynamic.appendChild(field("Base URL", "openai_base_url", s.openai_base_url, "https://api.openai.com/v1"));
        dynamic.appendChild(field("Model", "openai_model", s.openai_model, "gpt-4o-mini"));
        dynamic.appendChild(keyField("Works with OpenAI, Groq, OpenRouter, vLLM and LM Studio."));
      }
    }
    sel.onchange = renderDynamic;
    renderDynamic();
    wireSaveTest(card, { savePath: "/api/settings", testPath: "/api/settings/test" });
    return card;
  }

  function browserCard(s) {
    const card = el("div", "card");
    card.innerHTML =
      `<h3>${icon("globe")} Browser</h3>
       <div class="desc">Theta drives a real Chromium instance. Viewport
         <code>${esc(s.browser_size)}</code>, up to <code>${s.max_steps}</code> actions per task.</div>`;
    const mode = el("div", "banner " + (s.browser_headless ? "info" : "warn"));
    mode.innerHTML = icon(s.browser_headless ? "info" : "alert") + "<span>" + (s.browser_headless
      ? "Running <b>headless</b> — you watch through the live view. To see the real window and " +
        "take over yourself (which is how you handle logins), set <code>THETA_BROWSER_HEADLESS=0</code> and restart."
      : "Running <b>headful</b> — the browser window is visible and you can type in it yourself, " +
        "which is how you handle logins.") + "</span>";
    card.appendChild(mode);

    const safety = el("div", "banner info");
    safety.style.marginTop = "10px";
    safety.innerHTML = icon("shield") +
      "<span>Theta <b>never types passwords, card numbers or one-time codes</b>, and will not " +
      "touch a CAPTCHA. When a task needs one, it stops and asks you to do that part.</span>";
    card.appendChild(safety);
    return card;
  }

  function searchCard(s) {
    const card = el("div", "card");
    card.innerHTML =
      `<h3>${icon("search")} Web search &amp; research</h3>
       <div class="desc">Used to find a URL when you don't give one, and by the optional
         deep-research tool.</div>`;
    const provField = el("div", "field");
    provField.innerHTML = `<label>Search provider</label>`;
    const sel = el("select", "input");
    sel.dataset.field = "search_provider";
    s.search_providers.forEach((p) => {
      const o = el("option", "", SEARCH_LABELS[p] || p);
      o.value = p; if (p === s.search_provider) o.selected = true;
      sel.appendChild(o);
    });
    provField.appendChild(sel);
    card.appendChild(provField);

    const dynamic = el("div");
    card.appendChild(dynamic);
    function renderDynamic() {
      dynamic.innerHTML = "";
      if (sel.value === "duckduckgo") {
        const b = el("div", "banner info");
        b.innerHTML = icon("info") +
          "<span>No key needed. DuckDuckGo rate-limits automated use — if searches start failing, switch to Tavily.</span>";
        b.style.marginBottom = "14px";
        dynamic.appendChild(b);
        return;
      }
      const f = el("div", "field");
      f.innerHTML = `<label>API key</label>`;
      const i = el("input", "input");
      i.type = "password"; i.dataset.field = "search_api_key";
      i.placeholder = s.has_search_key ? s.search_key_masked : "Paste your key";
      f.appendChild(i);
      f.appendChild(el("div", "help", sel.value === "tavily"
        ? 'Free tier at <a href="https://tavily.com" target="_blank" rel="noopener">tavily.com</a>, no card needed.'
        : 'Key from <a href="https://brave.com/search/api/" target="_blank" rel="noopener">brave.com/search/api</a>.'));
      dynamic.appendChild(f);
    }
    sel.onchange = renderDynamic;
    renderDynamic();

    const tog = el("label", "toggle");
    tog.style.marginTop = "6px";
    tog.innerHTML = `<input type="checkbox" data-field="approve_research" ${s.approve_research ? "checked" : ""}>
      <span><span class="t-main">Show me the plan before deep research</span>
      <span class="t-sub">The research tool pauses so you can edit its sub-questions first.</span></span>`;
    card.appendChild(tog);
    wireSaveTest(card, { savePath: "/api/settings", testPath: "/api/settings/test-search",
                         testLabel: "Test search" });
    return card;
  }

  /* ======================================================================= *
   *  STATUS + BOOT                                                          *
   * ======================================================================= */
  async function refreshStatus() {
    try {
      const s = await getJSON("/api/status");
      state.status = s;
      $("#statusDot").className = "dot " + (s.model_ready ? "ok" : "warn");
      $("#statusText").textContent = s.model_ready ? s.model : "model not configured";
      $("#statusLine").title = `${s.tool_count} tools · ${s.transport}`;

      setBadge("#pbBadge", s.playbooks);
      setBadge("#runBadge", s.runs);
      setBadge("#schBadge", s.schedules_need_attention || s.schedules,
               !!s.schedules_need_attention);
      setBadge("#connBadge", (s.connected || []).length);
      return s;
    } catch (e) {
      $("#statusDot").className = "dot err";
      $("#statusText").textContent = "server unreachable";
    }
  }

  function setBadge(sel, value, alert) {
    const b = $(sel);
    if (!b) return;
    b.hidden = !value;
    b.textContent = value;
    b.className = "nav-badge" + (alert ? " alert" : "");
  }

  function autoGrow(t) {
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 168) + "px";
  }

  const AUTH_ERRORS = {
    state_mismatch: "That sign-in didn't match the request Theta started. Try again.",
    exchange_failed: "Google wouldn't complete the sign-in. Check the OAuth client settings.",
    access_denied: "Sign-in was cancelled, so Gmail is still disconnected.",
  };

  function handleReturnFromOAuth() {
    /* The OAuth callback can only hand information back through the URL, so read
       it once and scrub it — a shareable link should not carry a flow's outcome. */
    const q = new URLSearchParams(location.search);
    if (!q.has("view") && !q.has("connected") && !q.has("auth_error")) return null;
    let view = q.get("view");
    if (q.get("connected") === "google") {
      toast("Gmail connected", "ok");
      view = "connections";
    }
    const error = q.get("auth_error");
    if (error) {
      toast(AUTH_ERRORS[error] || `Sign-in failed (${error})`, "err");
      view = "connections";
    }
    history.replaceState({}, "", location.pathname);
    return view && TITLES[view] ? view : null;
  }

  function init() {
    // Hydrate the icons that live in the static shell.
    $$("[data-icon]").forEach((n) => { n.innerHTML = icon(n.dataset.icon); });
    initTheme();

    $$(".nav-item").forEach((b) => (b.onclick = () => setView(b.dataset.view)));
    $("#newTaskBtn").onclick = newTask;
    $("#menuBtn").onclick = () => document.body.classList.toggle("nav-open");

    const input = $("#composerInput");
    input.addEventListener("input", () => autoGrow(input));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendGoal(); }
    });
    $("#sendBtn").onclick = sendGoal;
    setBusy(false);

    const returned = handleReturnFromOAuth();
    refreshStatus().then(() => {
      renderHome();
      if (returned) setView(returned);
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
