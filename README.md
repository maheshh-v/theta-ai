---
title: Theta AI
emoji: 🤖
colorFrom: indigo
colorTo: blue
sdk: gradio
sdk_version: 6.22.0
app_file: app.py
pinned: false
license: mit
---

# 🤖 Theta AI — Personal AI Assistant

A personal AI agent that takes **natural-language commands** and performs real
tasks by calling tools through the **Model Context Protocol (MCP)**. Ask it to
check your inbox, draft a reply, look at your calendar, add a task, or search
your notes — and watch it **show its reasoning**: which tool it picked, why, the
arguments it passed, and the raw result.

Everything runs on **local mock data** (no Gmail/Google accounts, no OAuth), and
the default LLM is **Google Gemini's free tier** — with a one-line switch to a
local **Ollama** model, or a built-in **mock mode** that needs no key at all.

> Type *"Do I have any unread emails?"* → the agent decides to call the
> `email_list` tool with `{"unread_only": true}`, runs it over a live MCP
> session, and summarises the result. The reasoning panel shows every step.

---

## ✨ What it does

- **Chat UI (Gradio):** one box, type commands in plain English.
- **Real MCP tools:** three MCP servers (email, calendar/tasks, notes), each
  exposing its own set of tools over a stdio connection. Tools are **discovered
  at runtime** — the agent isn't hard-coded to what they can do.
- **Transparent agent loop:** a small controller lets the LLM decide which tool
  to call, calls it, feeds the result back, and repeats until it has an answer.
  Every step is displayed, not hidden.
- **Three tools, zero auth:**
  - 📧 **Email** — list/read/search a sample inbox, and *draft* replies (never sent).
  - 📅 **Calendar & Tasks** — view/add events, view/add/complete to-dos.
  - 📝 **Notes** — save and search notes.
- **Graceful everywhere:** if the LLM or a tool fails, the assistant explains what
  went wrong instead of crashing. If MCP subprocesses can't start (locked-down
  host), it transparently falls back to running the same tools in-process.

## 🧠 Architecture

The system is four layers, each independent and swappable.

```
  You (natural language)
        │
        ▼
  ┌───────────────┐     JSON action      ┌──────────────────────────┐
  │  Gradio UI    │ ───────────────────▶ │  Agent loop (orchestrator)│
  │  ui/app.py    │ ◀─────────────────── │  agent/orchestrator.py    │
  └───────────────┘   answer + trace     └────────────┬─────────────┘
                                                       │ decide (LLM)
                                        ┌──────────────▼─────────────┐
                                        │  LLM: Gemini / Ollama / Mock│
                                        │  agent/llm.py               │
                                        └──────────────┬─────────────┘
                                                       │ call_tool over MCP
                                        ┌──────────────▼─────────────┐
                                        │  MCP client manager         │
                                        │  tools/mcp_client.py        │
                                        └──────┬───────────┬──────────┘
                                     stdio     │           │    stdio
                              ┌───────────────▼─┐   ┌─────▼───────────────┐
                              │ MCP servers      │   │ ... email, calendar │
                              │ tools/servers/*  │   │     notes           │
                              └────────┬─────────┘   └─────────┬───────────┘
                                       └──── local JSON ───────┘
                                             data/*.json
```

**1. UI (`ui/app.py`)** — a Gradio chat interface. It doesn't know anything
about tools or LLMs; it just sends the typed command to the agent and renders
whatever comes back, including a collapsible reasoning panel.

**2. Agent loop (`agent/orchestrator.py`)** — the core control flow. On each
turn it builds a prompt listing the available tools, asks the LLM to decide
what to do next, and gets back one JSON object:

```json
{ "thought": "why I'm doing this", "action": "email_list", "action_input": {"unread_only": true} }
```

If `action` names a tool, the orchestrator calls it, appends the result to the
running transcript, and loops. If `action` is `"FINAL"`, it returns the answer
to the UI. A step cap (`MAX_AGENT_STEPS`) prevents runaway loops. Every step —
thought, tool, arguments, result, and whether it came from a live MCP call or
the fallback — is recorded and shown in the UI's reasoning panel.

**3. LLM layer (`agent/llm.py`)** — one interface, three interchangeable
providers: Gemini (REST calls to Google AI Studio), Ollama (REST calls to a
local server), or a keyless mock that uses simple keyword rules to pick a tool
so the whole loop still runs with zero setup. Provider choice is entirely
config-driven (`config.py` / `.env`); the orchestrator never knows which one is
active.

**4. Tools layer (`tools/`)** — the part that actually does things.
`tools/backends.py` holds the plain-Python logic (read/write local JSON).
`tools/servers/*.py` wraps that logic as MCP tools, one file per domain
(email, calendar, notes), each running as its own subprocess and speaking MCP
over stdio. `tools/mcp_client.py` is the client side: it spins up all three
server subprocesses, keeps their sessions alive in a background event loop,
discovers their tools automatically, and exposes a plain synchronous
`call_tool(name, args)` to the rest of the app. If a server process can't be
spawned (e.g. a locked-down host), the manager falls back to calling the same
backend functions in-process — the agent doesn't need to know the difference.

This separation means each layer can be swapped independently: point the LLM
layer at a different provider, add a new MCP server without touching the
agent, or replace the Gradio UI with something else, all without breaking the
others.

## 🗂️ Project structure

```
assistant/
├── app.py                  # Entry point (local run + HuggingFace Spaces)
├── config.py                # Env-driven settings (LLM choice, paths)
├── agent/
│   ├── llm.py                # Gemini / Ollama / Mock providers (one interface)
│   └── orchestrator.py       # The agent loop + reasoning trace
├── tools/
│   ├── backends.py           # Core tool logic over local JSON
│   ├── tool_specs.py         # Fallback tool catalogue (in-process safety net)
│   ├── mcp_client.py         # MCP client manager (stdio sessions, sync API)
│   └── servers/
│       ├── email_server.py     # MCP server: email
│       ├── calendar_server.py  # MCP server: calendar + tasks
│       └── notes_server.py     # MCP server: notes
├── ui/
│   └── app.py               # Gradio Blocks UI + reasoning display
├── data/
│   ├── emails.json           # Mock inbox
│   ├── calendar.json         # Mock events + tasks
│   └── notes.json            # Mock notes
├── requirements.txt
└── .env.example
```

## 🚀 Quickstart

```bash
# 1. Create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) configure an LLM — skip to run in mock mode
cp .env.example .env       # then paste a free GEMINI_API_KEY

# 4. Run
python app.py
```

Open http://127.0.0.1:7860 and start typing.

### Choosing an LLM

| Provider | Setup | Cost |
|----------|-------|------|
| **Gemini** (default) | Get a free key at [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey), put it in `.env` as `GEMINI_API_KEY` | Free tier |
| **Ollama** (local) | Install [Ollama](https://ollama.com), `ollama pull llama3.2`, set `LLM_PROVIDER=ollama` in `.env` | Free / offline |
| **Mock** (no key) | Do nothing — used automatically when no key is present | Free |

Mock mode is clearly labelled in the UI ("⚠️ demo mode"). It uses keyword
heuristics to pick a tool so the full loop and reasoning display still work.

### Try these commands

- `Do I have any unread emails?`
- `Draft a reply to Priya telling her I'll send the slides tomorrow morning`
- `What's on my calendar this week?`
- `Add a task to call the dentist, due 2026-08-12, high priority`
- `Save a note titled 'Demo ideas' with content 'Show the MCP reasoning panel'`
- `Search my notes about gifts`

## ➕ Adding a new tool

It takes three small steps — the agent discovers the new tool automatically, no
prompt edits needed:

1. **Write the logic** in `tools/backends.py` as a plain Python function
   (read/write local JSON in `data/`).
2. **Expose it** with an `@mcp.tool()` wrapper in the right file under
   `tools/servers/` (or add a new `*_server.py` and register it in
   `tools/mcp_client.py::_server_specs`). The function's **docstring becomes the
   description the LLM sees**, so write it for a reader.
3. *(optional)* Mirror it in `tools/tool_specs.py` so the in-process fallback
   knows about it too.

Example:

```python
# tools/backends.py
def notes_delete(note_id: str) -> dict:
    """Delete a note by id."""
    ...

# tools/servers/notes_server.py
@mcp.tool()
def notes_delete(note_id: str) -> dict:
    """Delete a saved note by its id (e.g. 'n2')."""
    return backends.notes_delete(note_id)
```

## ☁️ Deploy free on HuggingFace Spaces

1. Create a new **Space** → SDK: **Gradio**.
2. Upload this project (or push the repo). The YAML header at the top of this
   `README.md` configures the Space (`sdk: gradio`, `app_file: app.py`).
3. *(Optional)* In **Settings → Variables and secrets**, add `GEMINI_API_KEY`
   for real AI answers. Without it, the Space runs in mock mode.

That's it — `app.py` launches the Gradio app on the port the platform provides.

## 🛠️ Tech stack

- **Model Context Protocol (MCP)** — the tool servers and client session.
- **Gradio** — the web UI.
- **Google Gemini** (free tier) / **Ollama** / mock — the LLM, called over REST.

## 📄 License

MIT — see [LICENSE](LICENSE). The mock data in `data/` is fictional.

## 🔒 A note on safety

This is a demo on **local mock data**. Email replies are **drafted, never sent**.
There is no real Gmail/Calendar integration and no OAuth — swapping in real
integrations would mean replacing the functions in `tools/backends.py` with real
API calls (and adding the appropriate auth), while the agent and UI stay the same.
