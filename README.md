---
title: Theta AI
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# θ Theta — a personal AI agent

Theta takes **natural-language commands** and actually does things across your
**real Gmail and Google Calendar** — reading and searching mail, drafting and
(with your approval) sending replies, checking your schedule and adding events —
plus keeping local **notes and tasks** as its own memory. It decides which tool
to use, calls it over the **Model Context Protocol (MCP)**, and shows you a
concise, live trace of every step.

It is not a chatbot. A chatbot writes text; Theta takes actions on your accounts —
and asks first before it sends mail or changes your calendar.

> Type *"Do I have any unread emails?"* → Theta calls the `gmail_list` tool over a
> live MCP session, streams `Reading Gmail → Found 3 emails`, and answers.
> Ask it to *"reply to Priya and send it"* → it drafts the reply and **pauses for
> your approval** before anything leaves your outbox.

---

## ✨ Highlights

- **Real integrations, not mock data.** Gmail + Google Calendar over OAuth 2.0.
  Connect your account from the UI; disconnect any time.
- **Human-in-the-loop safety.** Sending email and any calendar change are
  **approval-gated** — Theta shows exactly what it will do and waits for a click.
- **Bring your own model.** A Settings page to choose the LLM provider
  (Google Gemini / local Ollama / a keyless mock) and set your own API key —
  masked in the UI, encrypted at rest, never logged.
- **Transparent execution.** A streamed timeline shows each tool, a one-line
  result, and its status — with raw details tucked behind a "developer details"
  disclosure. No raw chain-of-thought is exposed.
- **Real MCP architecture.** Four MCP tool servers run as separate subprocesses;
  tools are discovered at runtime, so adding one needs no changes to the agent.
- **Secure by construction.** Per-session encrypted token storage, CSRF-protected
  OAuth, log scrubbing, and least-privilege scopes.

## 🧠 Architecture

Four independent, swappable layers — the same clean separation the project was
built on, now with a real web/OAuth layer and real integrations.

```
  Browser SPA (vanilla JS, streamed trace + approval cards)
        │  REST + NDJSON stream          ▲ approve / reject
        ▼                                │
  FastAPI app  ──  encrypted per-session store (cookie → server-side tokens/keys)
   ├─ /api/chat (stream)   → Agent loop (resumable, approval-gated)
   ├─ /api/chat/resume     → resume a paused run
   ├─ /api/auth/google/*   → OAuth login / callback / disconnect
   ├─ /api/accounts        → connected-account status
   └─ /api/settings[/test] → LLM provider / key (masked, encrypted)
        │
        ▼
   Agent loop (agent/orchestrator.py) ── ReAct JSON controller, step cap
        │  call_tool(name, args, ctx)     (injects the session's Google token)
        ▼
   MCP client manager (tools/mcp_client.py) ── stdio sessions, sync API, fallback
        ├─ notes    (local JSON)
        ├─ tasks    (local JSON)
        ├─ gmail    (real Google API, token injected per call)
        └─ calendar (real Google API, token injected per call)
```

- **UI** (`server/static/`) — a self-contained SPA (Chat / Accounts / Settings /
  Activity). It knows nothing about tools or LLMs; it streams events and renders
  them.
- **Agent loop** (`agent/orchestrator.py`) — asks the LLM for one JSON action at a
  time, runs it, and repeats. It is **resumable**: when the model picks an
  approval-gated tool it pauses and hands control back to you.
- **LLM layer** (`agent/llm.py`) — one `complete()` interface over Gemini / Ollama
  / Mock, all via plain REST. Per-session config overrides the environment.
- **Tools** (`tools/`, `integrations/google/`) — Gmail/Calendar are stateless MCP
  servers; the manager injects the caller's access token at call time (the LLM
  never sees it) and scrubs it from the trace. Notes/Tasks are local JSON.

## 🚀 Run it locally

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate     macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional, but needed for real Gmail/Calendar
python app.py
```

Open **http://localhost:7860**. With no configuration Theta still runs: notes and
tasks work, and you can drive the whole agent loop with the keyless mock model —
Gmail/Calendar simply show a "Connect your Google account" state until you set up
OAuth below.

## 🔑 Connect Gmail + Calendar (Google Cloud setup)

Real Google access requires an OAuth client that **you** create (Google doesn't
let apps create it for you). One-time, ~5 minutes, in the
[Google Cloud Console](https://console.cloud.google.com):

1. **Create a project** (or pick one).
2. **Enable APIs** — *APIs & Services → Library* → enable **Gmail API** and
   **Google Calendar API**.
3. **OAuth consent screen** — choose **External**, fill the basics, and under
   **Test users** add your own Google address. (In "Testing" status, you plus up
   to 100 test users can use the app without Google verification.)
4. **Credentials → Create credentials → OAuth client ID → Web application.**
   Add this **Authorized redirect URI**:
   ```
   http://localhost:7860/api/auth/google/callback
   ```
   (For a deployed instance, also add `https://<your-domain>/api/auth/google/callback`.)
5. Copy the **Client ID** and **Client secret** into `.env`:
   ```
   GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=xxxx
   ```
6. Restart Theta, open **Accounts**, and click **Connect Google**.

Scopes requested (least-privilege): Gmail `readonly`, `compose`, `send`;
Calendar `readonly`, `events`; plus `openid email profile` to show which account
is connected.

## ⚙️ Configuration

All optional except the Google client (for real mail/calendar). See
[`.env.example`](.env.example) for the full list.

| Variable | Purpose |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Enable Gmail + Calendar OAuth |
| `PUBLIC_BASE_URL` | Public URL when deployed (builds the OAuth redirect) |
| `GEMINI_API_KEY` | Default Gemini key (users can also set their own in Settings) |
| `LLM_PROVIDER` | `gemini` / `ollama` / `mock` (auto-detected otherwise) |
| `THETA_SECRET_KEY` | Stable key that encrypts stored tokens/keys at rest |
| `THETA_HOST` / `THETA_PORT` | Bind address (default `127.0.0.1:7860`) |
| `THETA_COOKIE_SECURE` | Set to `1` when serving over HTTPS |

## 🔒 Security model

- **Tokens & keys never reach the browser.** A random session id lives in an
  encrypted, http-only cookie; the actual OAuth tokens and any user-set API key
  stay server-side, **encrypted at rest** with Fernet (`cryptography`).
- **Least privilege + explicit consent.** Only the scopes above; you approve on
  Google's own screen, and can disconnect (revoking the token) any time.
- **Approval gate.** `gmail_send_reply`, `calendar_add`, and `calendar_update`
  never run without an explicit click. No hard-delete tools ship.
- **No leaks.** Access tokens are injected server-side and scrubbed from the
  trace; a logging filter redacts any known secret; keys are masked in the UI.

## ➕ Add a new tool

Still three small steps — the agent discovers it automatically, no prompt edits:

1. Write the logic (a plain function in `tools/backends.py` for local data, or a
   function in `integrations/…` for an external API).
2. Wrap it with `@mcp.tool()` in the relevant `tools/servers/*_server.py` (or add
   a new server and register it in `tools/mcp_client.py::_server_specs`). The
   docstring becomes the description the LLM reads.
3. *(optional)* Mirror it in `tools/tool_specs.py` for the in-process fallback,
   and tag it in `tools/catalog.py` if it needs approval or Google auth.

## ☁️ Deploy

The included [`Dockerfile`](Dockerfile) serves the app on port 7860.

```bash
docker build -t theta .
docker run -p 7860:7860 --env-file .env theta
```

On **Hugging Face Spaces**, create a **Docker** Space and push this repo (the YAML
header above configures it). Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`PUBLIC_BASE_URL` (your Space URL), and a `THETA_SECRET_KEY` in
**Settings → Variables and secrets**, and add the Space's
`/api/auth/google/callback` URL to your OAuth client.

## ✅ Tests

```bash
pip install -r requirements-dev.txt
pytest          # unit + API tests (encryption, OAuth, tools, approval, settings)
python selftest.py   # boots the real MCP stack and the agent loop
```

## ⚠️ Honest limitations

- **Single instance = one shared secret.** Sessions isolate users' tokens, but a
  public instance should run privately or per-user; there's no full account
  system (Google is the only identity).
- **No conversation memory across turns** — each command is independent.
- **Email is reply-focused.** Theta drafts/sends *replies*; composing brand-new
  threads and Google Tasks are natural next tools (the architecture makes them
  easy to add).
- **Free-tier LLM quotas** are tight; Theta degrades gracefully to clear errors.

## 📄 License

MIT — see [LICENSE](LICENSE).
