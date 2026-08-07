/* ==========================================================================
   Theta SPA — vanilla JS, no build step.
   Talks to the FastAPI backend: streaming chat (NDJSON), OAuth accounts,
   LLM settings, and the tool catalogue.
   ========================================================================== */
(() => {
  "use strict";

  // ---- tiny helpers ------------------------------------------------------ //
  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // very small, safe-ish formatter: escapes first, then adds emphasis/lists.
  function formatText(text) {
    const escaped = esc(text);
    const lines = escaped.split("\n");
    let html = "";
    let inList = false;
    for (let line of lines) {
      const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
      if (bullet) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += "<li>" + inline(bullet[1]) + "</li>";
      } else {
        if (inList) { html += "</ul>"; inList = false; }
        if (line.trim() === "") html += "";
        else html += "<p>" + inline(line) + "</p>";
      }
    }
    if (inList) html += "</ul>";
    return html || "<p></p>";
  }
  const inline = (s) =>
    s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
     .replace(/`([^`]+)`/g, "<code>$1</code>");

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).message || (await r.json()).error || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  }
  const getJSON = (p) => api(p);
  const postJSON = (p, body) =>
    api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  async function* streamNDJSON(resp) {
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1);
        if (line.trim()) yield JSON.parse(line);
      }
    }
    if (buf.trim()) yield JSON.parse(buf);
  }

  function toast(message, kind = "ok") {
    const t = el("div", "toast " + kind, (kind === "err" ? "⚠️ " : "✓ ") + esc(message));
    $("#toast").appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, 3200);
  }

  // ---- state ------------------------------------------------------------- //
  const state = {
    view: "chat",
    accounts: null,
    settings: null,
    tools: null,
    messages: [],
    activity: JSON.parse(localStorage.getItem("theta_activity") || "[]"),
    busy: false,
  };

  // ---- theme ------------------------------------------------------------- //
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theta_theme", theme);
    $("#themeIcon").textContent = theme === "dark" ? "☀️" : "🌙";
    $("#themeLabel").textContent = theme === "dark" ? "Light" : "Dark";
  }
  function initTheme() {
    const saved = localStorage.getItem("theta_theme");
    const theme = saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    applyTheme(theme);
    $("#themeToggle").onclick = () =>
      applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
  }

  // ---- navigation -------------------------------------------------------- //
  const TITLES = {
    chat: ["Chat", "Ask Theta to act across your Gmail and Calendar."],
    accounts: ["Accounts", "Connect the services Theta can act on."],
    settings: ["Settings", "Choose your AI model and manage your API key."],
    activity: ["Activity", "A log of the tools Theta has run this session."],
  };
  function setView(view) {
    state.view = view;
    document.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === view));
    document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
    $("#view-" + view).classList.remove("hidden");
    $("#viewTitle").textContent = TITLES[view][0];
    $("#viewSub").textContent = TITLES[view][1];
    if (view === "accounts") renderAccounts();
    if (view === "settings") renderSettings();
    if (view === "activity") renderActivity();
  }

  // ---- account chip (topbar) -------------------------------------------- //
  function renderTopbar() {
    const g = state.accounts && state.accounts.google;
    const box = $("#topbarRight");
    box.innerHTML = "";
    if (g && g.connected) {
      const initial = (g.email || "?")[0].toUpperCase();
      const chip = el("div", "acct-chip",
        `<span class="avatar">${esc(initial)}</span>${esc(g.email)}`);
      box.appendChild(chip);
      $("#acctBadge").hidden = false; $("#acctBadge").textContent = "✓";
    } else if (g && g.configured) {
      const b = el("button", "btn primary small", "Connect Google");
      b.onclick = connectGoogle;
      box.appendChild(b);
      $("#acctBadge").hidden = true;
    } else {
      $("#acctBadge").hidden = true;
    }
  }

  // ---- CHAT -------------------------------------------------------------- //
  function renderChatEmpty() {
    const inner = $("#chatInner");
    inner.innerHTML = "";
    const connected = state.accounts && state.accounts.google && state.accounts.google.connected;
    const hero = el("div", "hero");
    hero.innerHTML = `
      <div class="mark">θ</div>
      <h2>Hi, I'm Theta</h2>
      <p>Tell me what you need and I'll use your Gmail, Calendar, notes and tasks to get it done — showing each step, and asking before I send or change anything.</p>`;
    inner.appendChild(hero);

    if (!connected && state.accounts && state.accounts.google && state.accounts.google.configured) {
      const banner = el("div", "banner info");
      banner.style.maxWidth = "460px"; banner.style.margin = "18px auto 0";
      banner.innerHTML = "🔗 Connect your Google account to unlock email and calendar. ";
      const link = el("a", "", "Go to Accounts →"); link.href = "#";
      link.onclick = (e) => { e.preventDefault(); setView("accounts"); };
      banner.appendChild(link);
      inner.appendChild(banner);
    }

    const chips = connected
      ? ["Do I have any unread emails?",
         "What's on my calendar this week?",
         "Draft a reply to my latest email saying I'll follow up tomorrow",
         "Add a task to review the budget, due 2026-08-20"]
      : ["What tasks do I have?",
         "Take a note titled 'Ideas' with 'Ship Theta v1'",
         "Add a task to call the dentist, due 2026-08-15, high priority"];
    const row = el("div", "chips");
    chips.forEach((c) => {
      const chip = el("button", "chip", esc(c));
      chip.onclick = () => { $("#composerInput").value = c; sendMessage(); };
      row.appendChild(chip);
    });
    inner.appendChild(row);
  }

  function addUserMessage(text) {
    const inner = $("#chatInner");
    const m = el("div", "msg user");
    m.innerHTML = `<div class="who">you</div><div class="bubble"></div>`;
    $(".bubble", m).textContent = text;
    inner.appendChild(m);
    scrollChat();
  }

  function addAssistantTurn() {
    const inner = $("#chatInner");
    const m = el("div", "msg assistant");
    m.innerHTML = `<div class="who">θ</div><div class="bubble">
      <div class="trace" hidden></div>
      <div class="thinking"><span class="d"></span><span class="d"></span><span class="d"></span></div>
      <div class="answer" hidden></div>
      <div class="approval-slot"></div>
      <div class="dev-slot"></div>
    </div>`;
    inner.appendChild(m);
    scrollChat();

    const traceEl = $(".trace", m), thinkEl = $(".thinking", m),
      answerEl = $(".answer", m), approvalSlot = $(".approval-slot", m),
      devSlot = $(".dev-slot", m);
    const steps = {};

    return {
      addStep(ev) {
        traceEl.hidden = false;
        const s = el("div", "trace-step running");
        s.dataset.i = ev.index;
        s.innerHTML = `<span class="tdot"></span><span class="tlabel">${esc(ev.label)}</span><span class="tsum"></span><span class="tsrc"></span>`;
        traceEl.appendChild(s);
        steps[ev.index] = s;
        scrollChat();
      },
      updateStep(ev) {
        const s = steps[ev.index];
        if (!s) return;
        s.className = "trace-step " + (ev.status || (ev.ok ? "done" : "error"));
        $(".tsum", s).textContent = ev.summary ? "— " + ev.summary : "";
        logActivity(ev);
        scrollChat();
      },
      showApproval(ev) {
        thinkEl.hidden = true;
        const card = el("div", "approval");
        card.innerHTML = `
          <div class="head"><span class="badge">Approval needed</span> ${esc(ev.label)}</div>
          <div class="body">${esc(ev.description)}</div>
          <div class="actions"></div>`;
        const actions = $(".actions", card);
        const approve = el("button", "btn success", "Approve & run");
        const reject = el("button", "btn danger-ghost", "Reject");
        approve.onclick = () => resolveApproval(this, ev, true, card);
        reject.onclick = () => resolveApproval(this, ev, false, card);
        actions.append(approve, reject);
        approvalSlot.appendChild(card);
        scrollChat();
      },
      thinking(on) { thinkEl.hidden = !on; },
      showFinal(ev) {
        thinkEl.hidden = true;
        if (ev.answer) { answerEl.hidden = false; answerEl.innerHTML = formatText(ev.answer); }
        if (ev.steps && ev.steps.length) renderDevDetails(devSlot, ev);
        scrollChat();
      },
      showError(msg) {
        thinkEl.hidden = true;
        answerEl.hidden = false;
        answerEl.innerHTML = `<p style="color:var(--danger)">⚠️ ${esc(msg)}</p>`;
      },
    };
  }

  function renderDevDetails(slot, ev) {
    slot.innerHTML = "";
    const d = el("details", "dev-details");
    const summary = el("summary", "", `Developer details · ${ev.steps.length} step(s) · via ${esc(ev.transport || "")}`);
    d.appendChild(summary);
    const pre = el("pre");
    pre.textContent = ev.steps.map((s) =>
      `[${s.status}] ${s.tool}  (${s.source})\nargs: ${JSON.stringify(s.args)}\n` +
      `observation: ${typeof s.observation === "string" ? s.observation : JSON.stringify(s.observation, null, 2)}`
    ).join("\n\n");
    d.appendChild(pre);
    slot.appendChild(d);
  }

  async function resolveApproval(turn, ev, approved, card) {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    if (!approved) card.querySelector(".head").innerHTML = `<span class="badge">Rejected</span> ${esc(ev.label)}`;
    turn.thinking(true);
    try {
      const resp = await fetch("/api/chat/resume", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: ev.run_id, approved }),
      });
      card.remove();
      await consume(resp, turn);
    } catch (e) { turn.showError(e.message); }
    finally { setBusy(false); }
  }

  async function consume(resp, turn) {
    for await (const ev of streamNDJSON(resp)) {
      if (ev.type === "tool_start") turn.addStep(ev);
      else if (ev.type === "tool_end") turn.updateStep(ev);
      else if (ev.type === "awaiting_approval") { setBusy(true); turn.showApproval(ev); }
      else if (ev.type === "final") turn.showFinal(ev);
    }
  }

  async function sendMessage() {
    const input = $("#composerInput");
    const text = input.value.trim();
    if (!text || state.busy) return;
    if (!state.messages.length) $("#chatInner").innerHTML = "";
    state.messages.push({ role: "user", content: text });
    input.value = ""; autoGrow(input);
    addUserMessage(text);
    const turn = addAssistantTurn();
    setBusy(true);
    try {
      const resp = await fetch("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      await consume(resp, turn);
    } catch (e) { turn.showError(e.message); }
    finally { if (!state.busy) setBusy(false); else setBusy(false); }
  }

  function setBusy(b) {
    state.busy = b;
    $("#sendBtn").disabled = b;
    $("#composerInput").disabled = b;
    $("#sendBtn").innerHTML = b ? '<span class="spinner"></span>' : "Send";
  }
  const scrollChat = () => { const s = $("#chatScroll"); s.scrollTop = s.scrollHeight; };

  // ---- ACTIVITY ---------------------------------------------------------- //
  function logActivity(ev) {
    state.activity.unshift({
      tool: ev.tool, label: ev.label, summary: ev.summary,
      status: ev.status, tag: ev.tag || "read", at: Date.now(),
    });
    state.activity = state.activity.slice(0, 60);
    localStorage.setItem("theta_activity", JSON.stringify(state.activity));
    $("#actBadge").hidden = false;
    $("#actBadge").textContent = state.activity.length;
  }
  function renderActivity() {
    const w = $("#activityWrap");
    if (!state.activity.length) {
      w.innerHTML = `<div class="empty"><div class="big">📊</div>
        <div>No activity yet. Tool calls will appear here as Theta works.</div></div>`;
      return;
    }
    const card = el("div", "card");
    card.appendChild(el("div", "section-title", "Recent tool calls"));
    state.activity.forEach((a) => {
      const item = el("div", "activity-item");
      const icon = a.status === "done" ? "✓" : a.status === "declined" ? "⤫" : a.status === "error" ? "!" : "•";
      item.innerHTML = `
        <div class="a-ic">${icon}</div>
        <div class="a-main">
          <div class="a-title">${esc(a.label || a.tool)} <span class="tag-pill ${a.tag}">${a.tag}</span></div>
          <div class="a-sub">${esc(a.summary || a.tool)}</div>
        </div>
        <div class="a-time">${timeAgo(a.at)}</div>`;
      card.appendChild(item);
    });
    w.innerHTML = "";
    const clear = el("button", "btn small", "Clear activity");
    clear.onclick = () => { state.activity = []; localStorage.removeItem("theta_activity"); $("#actBadge").hidden = true; renderActivity(); };
    w.append(card, clear);
  }
  function timeAgo(t) {
    const s = Math.floor((Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    return Math.floor(s / 3600) + "h ago";
  }

  // ---- ACCOUNTS ---------------------------------------------------------- //
  function connectGoogle() { window.location.href = "/api/auth/google/login"; }

  async function renderAccounts() {
    const w = $("#accountsWrap");
    w.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    let a;
    try { a = await getJSON("/api/accounts"); state.accounts = a; }
    catch (e) { w.innerHTML = `<div class="banner warn">Couldn't load accounts: ${esc(e.message)}</div>`; return; }
    const g = a.google;
    w.innerHTML = "";

    const card = el("div", "card");
    const row = el("div", "acct-row");
    row.innerHTML = `
      <div class="acct-logo">${googleLogo()}</div>
      <div class="acct-meta">
        <div class="name">Google — Gmail &amp; Calendar</div>
        <div class="status"></div>
      </div>
      <div class="acct-actions"></div>`;
    const statusEl = $(".status", row), actions = $(".acct-actions", row);

    if (!g.configured) {
      statusEl.textContent = "Not available — the server has no Google OAuth client configured.";
      card.append(row);
      const hint = el("div", "banner warn");
      hint.style.marginTop = "16px";
      hint.innerHTML = "To enable Google, the operator must set <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> (see the README setup checklist).";
      card.append(hint);
    } else if (g.connected) {
      statusEl.innerHTML = `Connected as <strong>${esc(g.email)}</strong>`;
      const dis = el("button", "btn small", "Disconnect");
      dis.onclick = async () => {
        dis.disabled = true;
        try { await postJSON("/api/auth/google/disconnect", {}); toast("Disconnected Google"); }
        catch (e) { toast(e.message, "err"); }
        await refreshAccounts(); renderAccounts();
      };
      actions.append(dis);
      card.append(row);
      const caps = el("div", "caps");
      const capDefs = [
        ["Read email", g.capabilities.gmail_read],
        ["Draft replies", g.capabilities.gmail_compose],
        ["Send email", g.capabilities.gmail_send],
        ["Read calendar", g.capabilities.calendar_read],
        ["Manage events", g.capabilities.calendar_write],
      ];
      capDefs.forEach(([label, on]) => caps.appendChild(el("span", "cap " + (on ? "on" : ""), (on ? "✓ " : "") + esc(label))));
      card.append(caps);
    } else {
      statusEl.textContent = "Not connected.";
      const btn = el("button", "btn primary small", "Connect Google");
      btn.onclick = connectGoogle;
      actions.append(btn);
      card.append(row);
      const note = el("div", "banner info");
      note.style.marginTop = "16px";
      note.textContent = "You'll be sent to Google's consent screen. Theta requests read/send access to Gmail and read/write access to Calendar. Tokens are stored encrypted and never shown to the browser.";
      card.append(note);
    }
    w.append(card);

    // Local stores note
    const local = el("div", "card");
    local.innerHTML = `<div class="section-title">Local to Theta</div>
      <div class="acct-row"><div class="acct-logo">🗒️</div>
      <div class="acct-meta"><div class="name">Notes &amp; Tasks</div>
      <div class="status">Stored locally as Theta's own memory — no account needed.</div></div></div>`;
    w.append(local);
  }

  // ---- SETTINGS ---------------------------------------------------------- //
  async function renderSettings() {
    const w = $("#settingsWrap");
    w.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    let s;
    try { s = await getJSON("/api/settings"); state.settings = s; }
    catch (e) { w.innerHTML = `<div class="banner warn">${esc(e.message)}</div>`; return; }

    w.innerHTML = "";
    const card = el("div", "card");
    card.innerHTML = `<h3>Language model</h3>
      <div class="desc">Active: <strong>${esc(s.active_label)}</strong>. Your settings apply to this browser session; the server's environment provides the default.</div>`;

    // Provider
    const provField = el("div", "field");
    provField.innerHTML = `<label>Provider</label>`;
    const sel = el("select", "input");
    s.providers.forEach((p) => {
      const o = el("option", "", p === "gemini" ? "Google Gemini" : p === "ollama" ? "Ollama (local)" : "Mock (no key, demo)");
      o.value = p; if (p === s.provider) o.selected = true; sel.appendChild(o);
    });
    provField.appendChild(sel);
    card.appendChild(provField);

    const dynamic = el("div");
    card.appendChild(dynamic);

    const result = el("div", "test-result");
    const actions = el("div", "form-actions");
    const saveBtn = el("button", "btn primary", "Save");
    const testBtn = el("button", "btn", "Test connection");
    actions.append(saveBtn, testBtn, result);
    card.appendChild(actions);
    w.appendChild(card);

    function renderDynamic() {
      const p = sel.value;
      dynamic.innerHTML = "";
      if (p === "gemini") {
        dynamic.appendChild(field("Model", "gemini_model", s.gemini_model, "e.g. gemini-flash-latest"));
        dynamic.appendChild(keyField(s));
      } else if (p === "ollama") {
        dynamic.appendChild(field("Host", "ollama_host", s.ollama_host, "http://localhost:11434"));
        dynamic.appendChild(field("Model", "ollama_model", s.ollama_model, "e.g. llama3.2"));
      } else {
        dynamic.appendChild(el("div", "banner info", "Mock mode needs no key — it uses keyword rules so the whole agent loop still runs for a zero-setup demo."));
      }
    }
    sel.onchange = () => { renderDynamic(); result.textContent = ""; };
    renderDynamic();

    function collect() {
      const data = { provider: sel.value };
      dynamic.querySelectorAll("[data-field]").forEach((inp) => {
        if (inp.dataset.field === "api_key") { if (inp.value.trim()) data.api_key = inp.value.trim(); }
        else data[inp.dataset.field] = inp.value.trim();
      });
      return data;
    }

    saveBtn.onclick = async () => {
      saveBtn.disabled = true; saveBtn.innerHTML = '<span class="spinner"></span>';
      try { state.settings = await postJSON("/api/settings", collect()); toast("Settings saved"); updateMcpStatus(); }
      catch (e) { toast(e.message, "err"); }
      saveBtn.disabled = false; saveBtn.textContent = "Save"; renderSettings();
    };
    testBtn.onclick = async () => {
      testBtn.disabled = true; testBtn.innerHTML = '<span class="spinner"></span> Testing';
      result.className = "test-result"; result.textContent = "";
      try {
        const r = await postJSON("/api/settings/test", collect());
        result.className = "test-result " + (r.ok ? "ok" : "err");
        result.textContent = (r.ok ? "✓ " : "⚠️ ") + r.message;
      } catch (e) { result.className = "test-result err"; result.textContent = "⚠️ " + e.message; }
      testBtn.disabled = false; testBtn.textContent = "Test connection";
    };
  }

  function field(label, name, value, ph) {
    const f = el("div", "field");
    f.innerHTML = `<label>${esc(label)}</label>`;
    const inp = el("input", "input");
    inp.dataset.field = name; inp.value = value || ""; inp.placeholder = ph || "";
    f.appendChild(inp);
    return f;
  }
  function keyField(s) {
    const f = el("div", "field");
    f.innerHTML = `<label>API key</label>`;
    const inp = el("input", "input");
    inp.type = "password"; inp.dataset.field = "api_key";
    inp.placeholder = s.has_api_key ? `${s.api_key_masked}  (${s.api_key_source})` : "Paste your Gemini API key";
    f.appendChild(inp);
    const help = el("div", "help",
      s.has_api_key
        ? "A key is set. Leave blank to keep it; type a new one to replace it. Keys are encrypted and never shown."
        : 'Get a free key at <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener">aistudio.google.com</a>.');
    f.appendChild(help);
    return f;
  }

  // ---- status + boot ----------------------------------------------------- //
  async function updateMcpStatus() {
    try {
      const t = await getJSON("/api/tools");
      state.tools = t;
      $("#mcpDot").className = "dot ok";
      $("#mcpText").textContent = `${t.count} tools · ${t.transport.split("(")[0].trim()}`;
    } catch (e) {
      $("#mcpDot").className = "dot warn";
      $("#mcpText").textContent = "tools unavailable";
    }
  }
  async function refreshAccounts() {
    try { state.accounts = await getJSON("/api/accounts"); renderTopbar(); }
    catch (e) {}
  }

  function autoGrow(t) { t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }

  function handleOAuthRedirect() {
    const q = new URLSearchParams(location.search);
    if (!q.has("connected") && !q.has("error") && !q.has("tab")) return;
    if (q.get("connected") === "google") toast("Google connected");
    if (q.has("error")) toast("Google connection failed: " + q.get("error"), "err");
    const tab = q.get("tab");
    history.replaceState({}, "", location.pathname);
    if (tab) setTimeout(() => setView(tab), 0);
  }

  function init() {
    initTheme();
    document.querySelectorAll(".nav-item").forEach((b) =>
      (b.onclick = () => setView(b.dataset.view)));
    const input = $("#composerInput");
    input.addEventListener("input", () => autoGrow(input));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    $("#sendBtn").onclick = sendMessage;
    if (state.activity.length) { $("#actBadge").hidden = false; $("#actBadge").textContent = state.activity.length; }

    Promise.all([updateMcpStatus(), refreshAccounts()]).then(() => {
      renderChatEmpty();
      handleOAuthRedirect();
    });
  }

  const googleLogo = () =>
    `<svg width="22" height="22" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.76h3.56c2.08-1.92 3.28-4.74 3.28-8.09Z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.56-2.76c-.98.66-2.23 1.06-3.72 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23Z"/><path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84Z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1A11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38Z"/></svg>`;

  document.addEventListener("DOMContentLoaded", init);
})();
