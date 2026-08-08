/* ==========================================================================
   Theta SPA — vanilla JS, no build step.

   Four views: Do (watch the agent work), Playbooks (saved automations),
   History (the audit trail), Settings.
   ========================================================================== */
(() => {
  "use strict";

  /* ---- helpers ---------------------------------------------------------- */
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
  const postJSON = (p, body, method) =>
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

  function toast(message, kind = "ok") {
    const t = el("div", "toast " + kind, esc(message));
    $("#toast").appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, 3200);
  }

  const fmtDate = (iso) => {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleString(undefined,
      { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  };

  function modal(node) {
    const box = $("#modalCard");
    box.innerHTML = "";
    box.appendChild(node);
    $("#modal").hidden = false;
  }
  function closeModal() { $("#modal").hidden = true; }
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  /* ---- state ------------------------------------------------------------ */
  const state = { view: "do", status: null, busy: false, hasMessages: false };

  /* ---- theme ------------------------------------------------------------ */
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theta_theme", theme);
    $("#themeIcon").textContent = theme === "dark" ? "☀" : "🌙";
    $("#themeLabel").textContent = theme === "dark" ? "Light" : "Dark";
  }
  function initTheme() {
    const saved = localStorage.getItem("theta_theme");
    applyTheme(saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
    $("#themeToggle").onclick = () =>
      applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
  }

  /* ---- navigation ------------------------------------------------------- */
  const TITLES = {
    do: ["Do", "Give Theta a goal on the web and watch it work."],
    playbooks: ["Playbooks", "Saved automations. These replay without a model."],
    history: ["History", "Every run Theta has made, step by step."],
    settings: ["Settings", "Model, connected accounts, search and how Theta behaves."],
  };
  function setView(view) {
    state.view = view;
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    $$(".view").forEach((v) => v.classList.add("hidden"));
    $("#view-" + view).classList.remove("hidden");
    $("#viewTitle").textContent = TITLES[view][0];
    $("#viewSub").textContent = TITLES[view][1];
    if (view === "playbooks") renderPlaybooks();
    if (view === "history") renderHistory();
    if (view === "settings") renderSettings();
  }

  /* ======================================================================= *
   *  LIVE BROWSER RAIL                                                      *
   * ======================================================================= */
  const live = {
    show() { $("#liveRail").hidden = false; },
    hide() { $("#liveRail").hidden = true; },
    active(on) { $("#liveDot").className = "live-dot" + (on ? " on" : ""); },
    reset() {
      $("#liveShot").innerHTML =
        '<div class="live-empty">The browser view appears here once Theta opens a page.</div>';
      $("#liveUrl").textContent = "about:blank";
      $("#liveStep").textContent = "";
      $("#liveNote").textContent = "";
    },
    update(ev) {
      if (ev.url) $("#liveUrl").textContent = ev.url;
      if (ev.index) $("#liveStep").textContent = `step ${ev.index}`;
      if (ev.record_id && ev.screenshot) {
        const src = `/api/runs/${encodeURIComponent(ev.record_id)}/shot/${encodeURIComponent(ev.screenshot)}`;
        const box = $("#liveShot");
        const img = el("img");
        img.alt = "Live browser view";
        img.src = src;
        img.onload = () => { box.innerHTML = ""; box.appendChild(img); };
      }
      if (ev.summary) $("#liveNote").textContent = ev.summary;
    },
  };

  /* ======================================================================= *
   *  DO                                                                     *
   * ======================================================================= */
  const EXAMPLES = [
    ["Collect data into a file",
     "Go to https://quotes.toscrape.com and collect the first 10 quotes with their authors and tags. Save it as quotes.csv"],
    ["Search and filter a site",
     "On https://quotes.toscrape.com/search.aspx, find quotes by Albert Einstein tagged 'change' and tell me what they are"],
    ["Read a page and summarise",
     "Open https://news.ycombinator.com and tell me the top 5 stories with their points"],
  ];

  function renderDoEmpty() {
    const inner = $("#chatInner");
    inner.innerHTML = "";
    state.hasMessages = false;
    live.hide();
    live.reset();

    const hero = el("div", "hero");
    hero.innerHTML = `
      <div class="mark">θ</div>
      <h2>Tell Theta what to do on the web.</h2>
      <p>It plans, drives a real browser step by step, and asks before anything
         irreversible. When it works, save it as a one-click automation.</p>`;
    inner.appendChild(hero);

    if (state.status && !state.status.model_ready) {
      const setup = el("div", "card");
      setup.style.maxWidth = "100%";
      setup.innerHTML = `<h3>One step before we start</h3>
        <div class="desc">Theta needs a language model to work out how to do things.
          A free Google AI Studio key takes about a minute, or point it at a local Ollama.</div>`;
      const go = el("button", "btn primary", "Open Settings");
      go.onclick = () => setView("settings");
      setup.appendChild(go);
      inner.appendChild(setup);
      return;
    }

    const chips = el("div", "chips");
    EXAMPLES.forEach(([title, goal]) => {
      const chip = el("button", "chip", `<b>${esc(title)}</b><br>${esc(goal)}`);
      chip.onclick = () => { $("#composerInput").value = goal; sendGoal(); };
      chips.appendChild(chip);
    });
    inner.appendChild(chips);
  }

  function addUserMessage(text) {
    const m = el("div", "msg user");
    m.innerHTML = `<div class="who">you</div><div class="bubble"></div>`;
    $(".bubble", m).textContent = text;
    $("#chatInner").appendChild(m);
    scrollChat();
  }

  function addAgentTurn() {
    const m = el("div", "msg assistant");
    m.innerHTML = `<div class="who">θ</div><div class="bubble">
      <div class="warn-slot"></div>
      <div class="trace"></div>
      <div class="approval-slot"></div>
      <div class="thinking"><span class="d"></span><span class="d"></span><span class="d"></span></div>
      <div class="answer" hidden></div>
      <div class="outputs"></div>
      <div class="save-slot"></div>
      <div class="dev-slot"></div>
    </div>`;
    $("#chatInner").appendChild(m);
    scrollChat();

    const traceEl = $(".trace", m), thinkEl = $(".thinking", m), answerEl = $(".answer", m);
    const approvalSlot = $(".approval-slot", m), saveSlot = $(".save-slot", m);
    const devSlot = $(".dev-slot", m), outEl = $(".outputs", m), warnSlot = $(".warn-slot", m);
    const steps = {};
    let recordId = "";
    const seenWarnings = new Set();

    return {
      get recordId() { return recordId; },
      started(ev) { recordId = ev.record_id || ""; live.show(); live.active(true); },

      toolStart(ev) {
        const s = el("div", "trace-step running");
        s.innerHTML = `<span class="tnum">${ev.index}</span>
          <span class="tmain"><span class="tlabel"></span> <span class="tsum"></span></span>`;
        $(".tlabel", s).textContent = ev.label || ev.tool;
        traceEl.appendChild(s);
        steps[ev.index] = s;
        live.active(true);
        scrollChat();
      },

      toolEnd(ev) {
        let s = steps[ev.index];
        if (!s) { this.toolStart(ev); s = steps[ev.index]; }
        s.className = "trace-step " + (ev.status || (ev.ok ? "done" : "error"));
        $(".tlabel", s).textContent = ev.label || ev.tool;
        $(".tsum", s).textContent = ev.summary ? "— " + ev.summary : "";
        if (!recordId && ev.record_id) recordId = ev.record_id;
        live.update(ev);
        (ev.warnings || []).forEach((w) => {
          if (seenWarnings.has(w)) return;
          seenWarnings.add(w);
          warnSlot.appendChild(el("div", "warn-note", "⚠ " + esc(w)));
        });
        scrollChat();
      },

      replay(ev) {
        if (ev.stage === "healing") {
          const n = el("div", "trace-step running");
          n.innerHTML = `<span class="tnum">↻</span><span class="tmain">
            <span class="tlabel">Re-finding an element</span>
            <span class="theal">the page changed since this was recorded</span></span>`;
          traceEl.appendChild(n);
          scrollChat();
        }
        if (ev.stage === "healed") toast("Playbook repaired and updated");
      },

      approval(ev) {
        thinkEl.hidden = true;
        live.active(false);
        const card = el("div", "approval");
        card.innerHTML = `
          <div class="head"><span class="badge">Your call</span> ${esc(ev.label || "Approval needed")}</div>
          <div class="body">
            <div class="what"></div>
            <div class="detail"></div>
          </div>
          <div class="actions"></div>`;
        $(".what", card).textContent = ev.description || "";

        // Two things stay editable on the card: a research plan, and the text of
        // an email before it leaves. Approving something you can't change first
        // isn't much of a decision. Everything else is one literal page action.
        const sending = ev.tool === "gmail_send_reply";
        let ta = null, field = null;
        if (ev.tool === "research" && ev.args && Array.isArray(ev.args.subquestions)) {
          field = "subquestions";
          ta = el("textarea");
          ta.spellcheck = false;
          ta.value = ev.args.subquestions.join("\n");
          $(".detail", card).appendChild(ta);
        } else if (sending && ev.args) {
          field = "body";
          $(".what", card).textContent =
            "Send this reply from your Gmail account — edit it here first if you like. " +
            "The recipient, subject and thread come from the message being replied to.";
          ta = el("textarea");
          ta.rows = 8;
          ta.value = ev.args.body || "";
          $(".detail", card).appendChild(ta);
        }
        const actions = $(".actions", card);
        const go = el("button", "btn success", sending ? "Send it" : "Approve & continue");
        const no = el("button", "btn danger-ghost", sending ? "Don't send" : "Skip this");
        go.onclick = () => {
          let args = null;
          if (field === "subquestions") {
            args = { ...ev.args,
                     subquestions: ta.value.split("\n").map((s) => s.trim()).filter(Boolean) };
          } else if (field === "body") {
            args = { ...ev.args, body: ta.value };
          }
          resolveApproval(this, ev, true, card, args);
        };
        no.onclick = () => resolveApproval(this, ev, false, card, null);
        actions.append(go, no);
        approvalSlot.appendChild(card);
        scrollChat();
      },

      thinking(on) { thinkEl.hidden = !on; },

      final(ev) {
        thinkEl.hidden = true;
        live.active(false);
        if (ev.record_id) recordId = ev.record_id;
        if (ev.answer) { answerEl.hidden = false; answerEl.innerHTML = formatText(ev.answer); }
        if (ev.setup_required) {
          const go = el("button", "btn primary small", "Open Settings");
          go.style.marginTop = "8px";
          go.onclick = () => setView("settings");
          answerEl.appendChild(go);
        }
        (ev.outputs || []).forEach((path) => {
          const a = el("a", "file-pill", "⤓ " + esc(path));
          a.href = "/api/files/" + path.split("/").map(encodeURIComponent).join("/");
          a.setAttribute("download", "");
          outEl.appendChild(a);
        });
        if (ev.can_save_playbook && recordId) this.offerPlaybook(recordId);
        if (ev.steps && ev.steps.length) renderDevDetails(devSlot, ev);
        refreshStatus();
        scrollChat();
      },

      offerPlaybook(runId) {
        const box = el("div", "save-pb");
        box.innerHTML = `<div><div class="sp-main">That worked — save it as a Playbook?</div>
          <div class="sp-sub">Re-runs the same steps with no model calls, in a fraction of the time.</div></div>`;
        const btn = el("button", "btn primary small", "Save as Playbook");
        btn.onclick = () => savePlaybookDialog(runId, box);
        box.appendChild(btn);
        saveSlot.appendChild(box);
      },

      error(msg) {
        thinkEl.hidden = true;
        live.active(false);
        answerEl.hidden = false;
        answerEl.innerHTML = `<p style="color:var(--danger)">⚠ ${esc(msg)}</p>`;
      },
    };
  }

  function renderDevDetails(slot, ev) {
    slot.innerHTML = "";
    const d = el("details", "dev-details");
    d.appendChild(el("summary", "",
      `Trace · ${ev.steps.length} step(s) · ${esc(ev.llm || "")} · via ${esc(ev.transport || "")}`));
    const pre = el("pre");
    pre.textContent = ev.steps.map((s) =>
      `[${s.status}] ${s.tool} (${s.source})\n  args: ${JSON.stringify(s.args)}\n  → ${s.summary}`
    ).join("\n");
    d.appendChild(pre);
    slot.appendChild(d);
  }

  async function savePlaybookDialog(runId, box) {
    const card = el("div");
    card.innerHTML = `<h3>Save as Playbook</h3>
      <div class="desc">Give it a name you'll recognise. The values you typed become
        inputs you can change on each run.</div>`;
    const nameField = el("div", "field");
    nameField.innerHTML = `<label>Name</label>`;
    const input = el("input", "input");
    input.placeholder = "e.g. Weekly price check";
    nameField.appendChild(input);
    card.appendChild(nameField);

    const actions = el("div", "form-actions");
    const save = el("button", "btn primary", "Save Playbook");
    const cancel = el("button", "btn ghost", "Cancel");
    save.onclick = async () => {
      save.disabled = true; save.innerHTML = '<span class="spinner"></span>';
      try {
        const pb = await postJSON("/api/playbooks", { run_id: runId, name: input.value.trim() });
        closeModal();
        toast(`Saved “${pb.name}”`);
        box.innerHTML = `<div class="sp-main">✓ Saved as a Playbook</div>`;
        const open = el("button", "btn small", "Open Playbooks");
        open.onclick = () => setView("playbooks");
        box.appendChild(open);
        refreshStatus();
      } catch (e) {
        toast(e.message, "err");
        save.disabled = false; save.textContent = "Save Playbook";
      }
    };
    cancel.onclick = closeModal;
    actions.append(save, cancel);
    card.appendChild(actions);
    modal(card);
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
    if (!state.hasMessages) { $("#chatInner").innerHTML = ""; state.hasMessages = true; }
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
    $("#sendBtn").disabled = b;
    $("#sendBtn").innerHTML = b ? '<span class="spinner"></span>' : "Run";
  }
  const scrollChat = () => { const s = $("#chatScroll"); s.scrollTop = s.scrollHeight; };

  async function newTask() {
    try { await postJSON("/api/do/clear", {}); } catch (e) {}
    setView("do");
    renderDoEmpty();
  }

  /* ======================================================================= *
   *  PLAYBOOKS                                                              *
   * ======================================================================= */
  async function renderPlaybooks() {
    const w = $("#playbooksWrap");
    w.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    let data;
    try { data = await getJSON("/api/playbooks"); }
    catch (e) { w.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }

    w.innerHTML = "";
    if (!data.playbooks.length) {
      w.appendChild(el("div", "empty", `<div class="big">⧉</div>
        <div class="etitle">No playbooks yet</div>
        <div>Run a task in <b>Do</b>. When it works, Theta offers to save it here —
        and replaying it needs no model at all.</div>`));
      return;
    }

    const grid = el("div", "grid");
    data.playbooks.forEach((pb) => {
      const card = el("div", "pb-card");
      card.innerHTML = `
        <div class="pt"></div>
        <div class="pg"></div>
        <div class="pm">
          <span>${pb.steps} steps</span>
          <span>${pb.params.length} input${pb.params.length === 1 ? "" : "s"}</span>
          <span>${pb.run_count} run${pb.run_count === 1 ? "" : "s"}</span>
          ${pb.last_status ? `<span class="pill ${pb.last_status === "done" ? "ok" : "bad"}">${esc(pb.last_status)}</span>` : ""}
        </div>
        <div class="pact"></div>`;
      $(".pt", card).textContent = pb.name;
      $(".pg", card).textContent = pb.goal;
      const act = $(".pact", card);
      const run = el("button", "btn primary small", "▶ Run");
      run.onclick = () => runPlaybookDialog(pb);
      const view = el("button", "btn small", "Steps");
      view.onclick = () => openPlaybook(pb.id);
      const del = el("button", "btn small danger-ghost", "Delete");
      del.onclick = async () => {
        if (!confirm(`Delete “${pb.name}”?`)) return;
        try { await api("/api/playbooks/" + pb.id, { method: "DELETE" });
              toast("Playbook deleted"); renderPlaybooks(); refreshStatus(); }
        catch (e) { toast(e.message, "err"); }
      };
      act.append(run, view, del);
      grid.appendChild(card);
    });
    w.appendChild(grid);
  }

  async function openPlaybook(pid) {
    const w = $("#playbooksWrap");
    w.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    let pb;
    try { pb = await getJSON("/api/playbooks/" + pid); }
    catch (e) { w.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }

    w.innerHTML = "";
    const wrap = el("div", "detail-wrap");
    const bar = el("div", "detail-bar");
    const back = el("button", "btn small ghost", "← All playbooks");
    back.onclick = renderPlaybooks;
    const run = el("button", "btn primary small", "▶ Run");
    run.onclick = () => runPlaybookDialog(pb);
    bar.append(back, run);
    wrap.appendChild(bar);

    const card = el("div", "card");
    card.style.maxWidth = "100%";
    card.innerHTML = `<h3>${esc(pb.name)}</h3>
      <div class="desc">${esc(pb.goal || "")}</div>`;
    if (pb.params.length) {
      card.appendChild(el("div", "section-title", "Inputs"));
      pb.params.forEach((p) => {
        const row = el("div", "step-row");
        row.innerHTML = `<span class="sn">⌨</span><span><b>${esc(p.label || p.name)}</b>
          <span style="color:var(--faint)"> — {{${esc(p.name)}}}, default “${esc(p.default)}”</span></span>`;
        card.appendChild(row);
      });
    }
    card.appendChild(el("div", "section-title", "Steps"));
    const list = el("div", "steps-list");
    (pb.step_descriptions || []).forEach((d, i) => {
      const row = el("div", "step-row");
      row.innerHTML = `<span class="sn">${i + 1}</span><span></span>`;
      row.lastChild.textContent = d;
      list.appendChild(row);
    });
    card.appendChild(list);
    wrap.appendChild(card);
    w.appendChild(wrap);
  }

  function runPlaybookDialog(pb) {
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
    const go = el("button", "btn primary", "▶ Run now");
    const cancel = el("button", "btn ghost", "Cancel");
    go.onclick = () => {
      const values = {};
      Object.entries(inputs).forEach(([k, i]) => (values[k] = i.value));
      closeModal();
      startReplay(pb, values);
    };
    cancel.onclick = closeModal;
    actions.append(go, cancel);
    card.appendChild(actions);
    modal(card);
  }

  async function startReplay(pb, values) {
    setView("do");
    $("#chatInner").innerHTML = "";
    state.hasMessages = true;
    live.show(); live.reset();
    addUserMessage(`▶ Run playbook: ${pb.name}`);
    const turn = addAgentTurn();
    setBusy(true);
    try {
      const resp = await fetch(`/api/playbooks/${encodeURIComponent(pb.id)}/run`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      await consume(resp, turn);
    } catch (e) { turn.error(e.message); }
    finally { setBusy(false); live.active(false); }
  }

  /* ======================================================================= *
   *  HISTORY                                                                *
   * ======================================================================= */
  async function renderHistory() {
    const w = $("#historyWrap");
    w.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    let data;
    try { data = await getJSON("/api/runs"); }
    catch (e) { w.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }

    w.innerHTML = "";
    if (!data.runs.length) {
      w.appendChild(el("div", "empty", `<div class="big">◷</div>
        <div class="etitle">No runs yet</div><div>Everything Theta does is recorded here.</div>`));
      return;
    }
    const grid = el("div", "grid");
    data.runs.forEach((r) => {
      const card = el("div", "pb-card");
      card.innerHTML = `
        <div class="pt" style="font-size:14px"></div>
        <div class="pm">
          <span>${esc(fmtDate(r.created))}</span>
          <span>${r.steps} steps</span>
          <span>${r.seconds}s</span>
          <span class="pill ${r.status === "done" ? "ok" : r.status === "failed" ? "bad" : ""}">${esc(r.status)}</span>
          ${r.playbook_id ? '<span class="pill">playbook</span>' : ""}
        </div>`;
      $(".pt", card).textContent = r.goal;
      card.style.cursor = "pointer";
      card.onclick = () => openRun(r.id);
      grid.appendChild(card);
    });
    w.appendChild(grid);
  }

  async function openRun(runId) {
    const w = $("#historyWrap");
    w.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    let r;
    try { r = await getJSON("/api/runs/" + runId); }
    catch (e) { w.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }

    w.innerHTML = "";
    const wrap = el("div", "detail-wrap");
    const bar = el("div", "detail-bar");
    const back = el("button", "btn small ghost", "← All runs");
    back.onclick = renderHistory;
    const del = el("button", "btn small danger-ghost", "Delete run");
    del.onclick = async () => {
      if (!confirm("Delete this run and its screenshots?")) return;
      try { await api("/api/runs/" + runId, { method: "DELETE" });
            toast("Run deleted"); renderHistory(); refreshStatus(); }
      catch (e) { toast(e.message, "err"); }
    };
    bar.append(back, del);
    wrap.appendChild(bar);

    const card = el("div", "card");
    card.style.maxWidth = "100%";
    const h = el("h3"); h.textContent = r.goal; card.appendChild(h);
    card.appendChild(el("div", "desc",
      `${esc(fmtDate(r.created))} · ${r.steps.length} steps · ${r.seconds}s · ${esc(r.status)}` +
      (r.model ? ` · ${esc(r.model)}` : "")));

    const shots = r.steps.filter((s) => s.screenshot);
    if (shots.length) {
      const strip = el("div", "shot-strip");
      shots.forEach((s) => {
        const img = el("img");
        img.src = `/api/runs/${encodeURIComponent(r.id)}/shot/${encodeURIComponent(s.screenshot)}`;
        img.alt = `Step ${s.index}`;
        img.onclick = () => {
          const big = el("div");
          big.innerHTML = `<h3>Step ${s.index} — ${esc(s.label || s.tool)}</h3>
            <div class="desc">${esc(s.url || "")}</div>`;
          const full = el("img"); full.src = img.src;
          big.appendChild(full);
          modal(big);
        };
        strip.appendChild(img);
      });
      card.appendChild(strip);
    }

    card.appendChild(el("div", "section-title", "Steps"));
    const list = el("div", "steps-list");
    r.steps.forEach((s) => {
      const row = el("div", "step-row " + (s.ok ? (s.healed ? "healed" : "") : "error"));
      row.innerHTML = `<span class="sn">${s.index}</span><span class="smain"></span>`;
      $(".smain", row).innerHTML =
        `<b>${esc(s.label || s.tool)}</b> <span style="color:var(--muted)">${esc(s.summary || "")}</span>` +
        (s.healed ? ' <span class="pill">re-found</span>' : "");
      list.appendChild(row);
    });
    card.appendChild(list);

    if (r.answer) {
      card.appendChild(el("div", "section-title", "Result"));
      const ans = el("div", "answer");
      ans.innerHTML = formatText(r.answer);
      card.appendChild(ans);
    }
    (r.outputs || []).forEach((path) => {
      const a = el("a", "file-pill", "⤓ " + esc(path));
      a.href = "/api/files/" + path.split("/").map(encodeURIComponent).join("/");
      a.setAttribute("download", "");
      card.appendChild(a);
    });
    wrap.appendChild(card);
    w.appendChild(wrap);
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
    const w = $("#settingsWrap");
    w.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    let s;
    try { s = await getJSON("/api/settings"); }
    catch (e) { w.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
    w.innerHTML = "";
    w.appendChild(modelCard(s));
    w.appendChild(connectionsCard(s));
    w.appendChild(browserCard(s));
    w.appendChild(searchCard(s));
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

  function wireSaveTest(card, { savePath, testPath, testLabel }) {
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
      try { await postJSON(savePath, collect(card)); toast("Saved"); await refreshStatus(); renderSettings(); }
      catch (e) { toast(e.message, "err"); saveBtn.disabled = false; saveBtn.textContent = "Save"; }
    };
    if (testBtn) {
      testBtn.onclick = async () => {
        testBtn.disabled = true; testBtn.innerHTML = '<span class="spinner"></span> Testing';
        result.className = "test-result"; result.textContent = "";
        try {
          const r = await postJSON(testPath, collect(card));
          result.className = "test-result " + (r.ok ? "ok" : "err");
          result.textContent = (r.ok ? "✓ " : "⚠ ") + r.message;
        } catch (e) { result.className = "test-result err"; result.textContent = "⚠ " + e.message; }
        testBtn.disabled = false; testBtn.textContent = testLabel || "Test connection";
      };
    }
  }

  function modelCard(s) {
    const card = el("div", "card");
    card.innerHTML = `<h3>Language model</h3>
      <div class="desc">Active: <strong>${esc(s.active_label)}</strong>. This is what
        works out <em>how</em> to do a task. Playbooks replay without it.</div>`;
    if (!s.model_ready) {
      const b = el("div", "banner warn",
        `⚠ ${esc(s.model_error || "No model configured.")} Theta can't plan a task until this is set.`);
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
        dynamic.appendChild(el("div", "banner info",
          "Fully offline. Browser operation asks a lot of a model — 8B+ is the realistic floor."));
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

  function connectionsCard(s) {
    const c = s.connections || { notion: {}, google: {} };
    const card = el("div", "card");
    card.innerHTML = `<h3>Connections</h3>
      <div class="desc">Accounts Theta reaches through their own APIs instead of by
        driving the website. Faster, and it works headless — the browser can't log in,
        because Theta never types a password.</div>`;

    /* --- Notion ---------------------------------------------------------- */
    card.appendChild(el("div", "section-title", "Notion"));
    const n = c.notion || {};
    if (n.connected) {
      card.appendChild(el("div", "banner ok",
        `✓ Connected with <code>${esc(n.token_masked)}</code> (from ${esc(n.token_source)}).
         Theta can only see pages you've shared with the integration.`));
    }
    const nf = el("div", "field");
    nf.innerHTML = `<label>Integration token</label>`;
    const ni = el("input", "input");
    ni.type = "password";
    ni.dataset.field = "notion_token";
    ni.placeholder = n.connected ? n.token_masked : "ntn_…";
    nf.appendChild(ni);
    nf.appendChild(el("div", "help",
      'Create an internal integration at <a href="https://www.notion.so/my-integrations" ' +
      'target="_blank" rel="noopener">notion.so/my-integrations</a>, copy its secret, then ' +
      'open each page or database you want Theta to reach and use <b>⋯ → Connections</b> to ' +
      'add it. Encrypted at rest, never sent back to this page.'));
    card.appendChild(nf);

    /* --- Gmail ----------------------------------------------------------- */
    card.appendChild(el("div", "section-title", "Gmail"));
    const g = c.google || {};
    if (!g.configured) {
      card.appendChild(el("div", "banner info",
        "Gmail needs a Google OAuth client, which belongs to this deployment rather than " +
        "to you. Set <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> " +
        "and restart — see <code>.env.example</code>."));
    } else if (g.connected) {
      card.appendChild(el("div", "banner ok",
        `✓ Connected as <b>${esc(g.email || g.name || "your account")}</b>. Theta can read
         and draft, and can only send after you approve each message.`));
      const off = el("button", "btn danger-ghost small", "Disconnect Gmail");
      off.onclick = async () => {
        off.disabled = true;
        try {
          await postJSON("/api/auth/google/disconnect", {});
          toast("Gmail disconnected");
          renderSettings();
        } catch (e) { toast(e.message, "err"); off.disabled = false; }
      };
      card.appendChild(off);
    } else {
      card.appendChild(el("div", "banner info",
        "You'll sign in on Google's own page. Theta asks only to read mail, save drafts " +
        "and send — never to delete or modify anything — and never sees your password."));
      const on = el("button", "btn primary small", "Connect Gmail");
      on.onclick = () => { location.href = "/api/auth/google/login"; };
      card.appendChild(on);
    }

    wireSaveTest(card, { savePath: "/api/settings", testPath: "/api/settings/test-notion",
                         testLabel: "Test Notion" });
    return card;
  }

  function browserCard(s) {
    const card = el("div", "card");
    card.innerHTML = `<h3>Browser</h3>
      <div class="desc">Theta drives a real Chromium instance. Viewport
        <code>${esc(s.browser_size)}</code>, up to <code>${s.max_steps}</code> actions per task.</div>`;
    card.appendChild(el("div", "banner " + (s.browser_headless ? "info" : "warn"),
      s.browser_headless
        ? "Running <b>headless</b> — you see it through the live view. To watch the real window and take over yourself (needed for logins), set <code>THETA_BROWSER_HEADLESS=0</code> and restart."
        : "Running <b>headful</b> — the browser window is visible and you can type in it yourself, which is how you handle logins."));
    const note = el("div", "banner info");
    note.style.marginTop = "12px";
    note.innerHTML = "Theta <b>never types passwords, card numbers or one-time codes</b>, " +
      "and will not touch a CAPTCHA. When a task needs one, it stops and asks you to do that part.";
    card.appendChild(note);
    return card;
  }

  function searchCard(s) {
    const card = el("div", "card");
    card.innerHTML = `<h3>Web search &amp; research</h3>
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
        const b = el("div", "banner info",
          "No key needed. DuckDuckGo rate-limits automated use — if searches start failing, switch to Tavily.");
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
      <span><span class="tmain2">Show me the plan before deep research</span>
      <span class="tsub">The research tool pauses so you can edit its sub-questions first.</span></span>`;
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
      const pb = $("#pbBadge"); pb.hidden = !s.playbooks; pb.textContent = s.playbooks;
      const rb = $("#runBadge"); rb.hidden = !s.runs; rb.textContent = s.runs;
      return s;
    } catch (e) {
      $("#statusDot").className = "dot err";
      $("#statusText").textContent = "server unreachable";
    }
  }

  function autoGrow(t) {
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 170) + "px";
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
    const view = q.get("view");
    if (q.get("connected") === "google") toast("Gmail connected");
    const error = q.get("auth_error");
    if (error) toast(AUTH_ERRORS[error] || `Sign-in failed (${error})`, "err");
    history.replaceState({}, "", location.pathname);
    return view && TITLES[view] ? view : null;
  }

  function init() {
    initTheme();
    $$(".nav-item").forEach((b) => (b.onclick = () => setView(b.dataset.view)));
    $("#newTaskBtn").onclick = newTask;
    const input = $("#composerInput");
    input.addEventListener("input", () => autoGrow(input));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendGoal(); }
    });
    $("#sendBtn").onclick = sendGoal;
    const returned = handleReturnFromOAuth();
    refreshStatus().then(() => {
      renderDoEmpty();
      if (returned) setView(returned);
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
