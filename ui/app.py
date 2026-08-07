"""
Gradio chat UI for the Personal AI Assistant.

The chat box takes natural-language commands. For every turn the assistant
shows its final answer AND an expandable "Agent reasoning" panel revealing which
tool it chose, why, the arguments it passed, whether the call went over MCP or
the in-process fallback, and the raw tool observation — so it's visible that the
AI is genuinely deciding and acting through tools.
"""

from __future__ import annotations

import json
import os

import gradio as gr

from agent.llm import build_llm
from config import settings
from tools.mcp_client import MCPManager

# --------------------------------------------------------------------------- #
# One-time startup: connect MCP servers and build the agent.                  #
# --------------------------------------------------------------------------- #
MANAGER = MCPManager()
MANAGER.start()

# Imported here (after settings) so build_llm sees a fully-resolved config.
from agent.orchestrator import Agent  # noqa: E402

LLM = build_llm()
AGENT = Agent(MANAGER, LLM)

EXAMPLES = [
    "Do I have any unread emails?",
    "Draft a reply to Priya telling her I'll send the slides tomorrow morning",
    "What's on my calendar this week?",
    "Add a task to call the dentist, due 2026-08-12, high priority",
    "Save a note titled 'Demo ideas' with content 'Show the MCP reasoning panel'",
    "Search my notes about gifts",
]


# --------------------------------------------------------------------------- #
# Rendering helpers                                                           #
# --------------------------------------------------------------------------- #
def _status_md() -> str:
    st = MANAGER.status()
    dot = "🟢" if st["connected"] else "🟡"
    servers = ", ".join(st["servers"]) if st["servers"] else "—"
    mock_note = "  ·  ⚠️ demo mode" if getattr(LLM, "is_mock", False) else ""
    return (
        f"**LLM:** {LLM.label}{mock_note}  \n"
        f"**Tool transport:** {dot} {st['transport']}  ·  "
        f"**servers:** {servers}  ·  **tools:** {st['tool_count']}"
    )


def _tools_md() -> str:
    tools = MANAGER.list_tools()
    by_server: dict[str, list] = {}
    for t in tools:
        by_server.setdefault(t.server, []).append(t)
    lines = []
    for server in sorted(by_server):
        lines.append(f"**`{server}` server**")
        for t in by_server[server]:
            props = (t.input_schema or {}).get("properties", {})
            sig = ", ".join(props.keys())
            lines.append(f"- `{t.name}({sig})` — {t.description}")
        lines.append("")
    return "\n".join(lines)


def _truncate(text: str, n: int = 1500) -> str:
    return text if len(text) <= n else text[:n] + "\n… (truncated)"


def _reasoning_md(result) -> str:
    """Collapsible panel describing every step the agent took."""
    if not result.steps:
        return (
            "\n\n<details><summary>🧠 Agent reasoning</summary>\n\n"
            "_Answered directly — no tool call was needed._\n\n</details>"
        )

    n = len(result.steps)
    head = (
        f"\n\n<details><summary>🧠 Agent reasoning "
        f"({n} step{'s' if n != 1 else ''} · via {result.transport})</summary>\n\n"
    )
    body = []
    for s in result.steps:
        ok = "✅" if s.ok else "❌"
        body.append(f"**Step {s.index} — chose `{s.tool}`** {ok} _(source: {s.source})_")
        if s.thought:
            body.append(f"> {s.thought}")
        body.append(f"**Arguments:** `{json.dumps(s.args, ensure_ascii=False)}`")
        obs = s.observation
        obs_str = obs if isinstance(obs, str) else json.dumps(obs, ensure_ascii=False, indent=2)
        body.append("**Observation:**\n```json\n" + _truncate(obs_str) + "\n```")
        body.append("")
    tail = "</details>"
    return head + "\n".join(body) + "\n" + tail


def _assistant_message(result) -> str:
    answer = result.answer or "_(no answer)_"
    return answer + _reasoning_md(result)


# --------------------------------------------------------------------------- #
# Chat handlers                                                               #
# --------------------------------------------------------------------------- #
def respond(message: str, history: list):
    message = (message or "").strip()
    if not message:
        return history, ""
    history = history + [{"role": "user", "content": message}]
    try:
        result = AGENT.run(message)
        reply = _assistant_message(result)
    except Exception as ex:  # pragma: no cover - last-resort guard
        reply = (
            "⚠️ Something went wrong while handling that command.\n\n"
            f"```\n{type(ex).__name__}: {ex}\n```"
        )
    history = history + [{"role": "assistant", "content": reply}]
    return history, ""


def use_example(example: str):
    return example


# --------------------------------------------------------------------------- #
# UI                                                                          #
# --------------------------------------------------------------------------- #
HOW_TO_ADD_TOOL = """\
Adding a new MCP tool takes three small steps:

1. **Write the logic** in `tools/backends.py` as a plain Python function
   (it reads/writes local JSON in `data/`).
2. **Expose it over MCP** by adding an `@mcp.tool()` wrapper in the relevant
   file under `tools/servers/` (or create a new `*_server.py` and register it in
   `tools/mcp_client.py::_server_specs`). The docstring becomes the tool
   description the LLM sees.
3. *(optional)* Mirror it in `tools/tool_specs.py` so the in-process fallback
   knows about it too.

That's it — the agent discovers the new tool automatically at startup via
`session.list_tools()`; no prompt changes required.
"""


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="Personal AI Assistant (MCP)", fill_height=True) as demo:
        gr.Markdown(
            "# 🤖 Personal AI Assistant\n"
            "Type a natural-language command. The agent decides which **MCP tool** "
            "to call, calls it, and shows its reasoning. All data is local mock data "
            "— no accounts or auth needed."
        )
        status = gr.Markdown(_status_md())

        chatbot = gr.Chatbot(
            height=460,
            label="Assistant",
            render_markdown=True,
        )

        with gr.Row():
            msg = gr.Textbox(
                placeholder="e.g. “Do I have any unread emails?”",
                scale=8,
                show_label=False,
                autofocus=True,
            )
            send = gr.Button("Send", variant="primary", scale=1)
            clear = gr.Button("Clear", scale=1)

        gr.Examples(examples=EXAMPLES, inputs=msg, label="Try an example")

        with gr.Accordion("🛠️  Available MCP tools", open=False):
            gr.Markdown(_tools_md())
        with gr.Accordion("➕  How to add a new tool", open=False):
            gr.Markdown(HOW_TO_ADD_TOOL)

        # Wiring
        send.click(respond, [msg, chatbot], [chatbot, msg])
        msg.submit(respond, [msg, chatbot], [chatbot, msg])
        clear.click(lambda: ([], ""), None, [chatbot, msg])

    return demo


def main() -> None:
    demo = build_demo()
    demo.queue()
    demo.launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        show_error=True,
    )


if __name__ == "__main__":
    main()
