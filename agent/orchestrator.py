"""
The agent loop.

A small, transparent ReAct-style controller (the same shape LangGraph builds
for tool-calling agents): on each step the LLM emits a JSON action — either call
a tool or finish — and we execute tool calls over MCP, feed the observation
back, and repeat until the model produces a FINAL answer or we hit the step cap.

Every step (thought, chosen tool, arguments, observation, and whether it came
from MCP or the fallback) is recorded in `AgentResult.steps` so the UI can show
exactly how the assistant reasoned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from typing import Callable

from agent.llm import BaseLLM, LLMError, build_llm
from config import settings
from tools import catalog
from tools.mcp_client import MCPManager, ToolContext, ToolInfo

SYSTEM_PROMPT = """\
You are a personal AI assistant that completes real tasks by calling tools.
You have NO direct access to the user's data — to read or change anything you
must call a tool.

On each turn respond with EXACTLY ONE JSON object and nothing else:
{
  "thought": "<1-2 sentences: what to do next and why>",
  "action": "<a tool name from AVAILABLE TOOLS, or the literal string FINAL>",
  "action_input": <an object of arguments for the tool, OR (when action is FINAL) a string: your final answer to the user>
}

Rules:
- Use ONLY tools listed in AVAILABLE TOOLS, with valid arguments.
- Prefer the fewest calls needed; often a single tool call is enough.
- Base every answer on tool observations — never invent emails, events, or notes.
- When you have what you need, use action "FINAL" with a clear, friendly reply.
- If the user just chats or asks something no tool covers, you may answer with FINAL directly.
"""


@dataclass
class Step:
    index: int
    thought: str
    tool: str | None
    args: dict
    observation: object
    source: str
    ok: bool
    label: str = ""
    summary: str = ""
    tag: str = "read"
    status: str = "done"  # done | declined | error


@dataclass
class AgentResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    llm_label: str = ""
    transport: str = ""
    error: str | None = None


@dataclass
class PendingApproval:
    """Returned by a run when the model wants to perform an approval-gated action
    (send mail, change a calendar). The caller decides, then resumes the run."""

    index: int
    tool: str
    args: dict
    thought: str
    label: str
    description: str


_LLM_UNAVAILABLE = (
    "⚠️ The language model is unavailable, so I couldn't process that.\n\n"
    "Reason: {reason}\n\n"
    "Tip: open Settings to configure an API key (e.g. Gemini), run a local "
    "Ollama, or leave both unset to use the built-in mock mode."
)


class Agent:
    def __init__(self, manager: MCPManager, llm: BaseLLM | None = None) -> None:
        self.mgr = manager
        self.llm = llm or build_llm()

    def start(
        self,
        command: str,
        context: ToolContext | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> "AgentRun":
        """Begin a resumable run. Call `.advance()` to drive it; it returns a
        `PendingApproval` when it needs a decision, or an `AgentResult` when done."""
        return AgentRun(self, command, context, on_event)

    def run(self, command: str, context: ToolContext | None = None) -> AgentResult:
        """Convenience driver for non-interactive callers (tests, read-only use).
        Any approval-gated action is DECLINED — this path never sends or writes."""
        run = self.start(command, context)
        outcome = run.advance()
        while isinstance(outcome, PendingApproval):
            outcome = run.advance(approved=False)
        return outcome


class AgentRun:
    """One resumable execution of the agent loop. Emits trace events as it goes
    and pauses (via a generator `yield`) whenever an action needs approval."""

    def __init__(self, agent: Agent, command: str,
                 context: ToolContext | None = None,
                 on_event: Callable[[dict], None] | None = None) -> None:
        self.agent = agent
        self.command = (command or "").strip()
        self.context = context
        self.on_event = on_event
        self.result = AgentResult(
            answer="", llm_label=agent.llm.label, transport=agent.mgr.transport
        )
        self.pending: PendingApproval | None = None
        self.finished = False
        self._started = False

        tools = agent.mgr.list_tools()
        self._tools_desc = _render_tools(tools)
        self._by_name = {t.name: t for t in tools}
        self._transcript: list[str] = []
        self._gen = self._iter()

    # -- driving ----------------------------------------------------------- #
    def advance(self, approved: bool | None = None):
        """Run until the next approval pause or completion. Returns a
        `PendingApproval` or the final `AgentResult`."""
        if self.finished:
            return self.result
        try:
            value = None if not self._started else approved
            self._started = True
            self.pending = self._gen.send(value)
            return self.pending
        except StopIteration:
            self.finished = True
            self.pending = None
            return self.result

    # -- the loop (generator; yields at approval points) ------------------- #
    def _iter(self):
        if not self.command:
            self.result.answer = "Please type a command, e.g. “what's on my calendar?”"
            return

        llm, mgr = self.agent.llm, self.agent.mgr

        for step_no in range(1, settings.max_agent_steps + 1):
            prompt = _build_user_prompt(self.command, self._tools_desc, self._transcript)
            try:
                raw = llm.complete(SYSTEM_PROMPT, prompt)
            except LLMError as ex:
                self.result.error = str(ex)
                self.result.answer = _LLM_UNAVAILABLE.format(reason=ex)
                return

            action = _parse_action(raw)
            if action is None:  # prose, not JSON → treat as the final answer
                self.result.answer = raw.strip() or "(the model returned an empty response)"
                return

            thought = str(action.get("thought", "")).strip()
            name = str(action.get("action", "")).strip()
            action_input = action.get("action_input", {})

            if name.upper() == "FINAL" or name == "":
                self.result.answer = _as_answer(action_input) or "(no answer produced)"
                return

            if name not in self._by_name:
                observation = {
                    "error": f"'{name}' is not an available tool. "
                    f"Choose from: {', '.join(sorted(self._by_name))}."
                }
                self._record(step_no, thought, name, _as_args(action_input),
                             observation, source="—", ok=False, status="error")
                continue

            tool = self._by_name[name]
            args = _as_args(action_input)
            self._emit({"type": "tool_start", "index": step_no, "tool": name,
                        "label": catalog.label_for(name), "tag": tool.tag, "args": args})

            # Approval gate: pause the run and hand control back to the caller.
            if tool.tag == "confirm":
                pend = PendingApproval(
                    index=step_no, tool=name, args=args, thought=thought,
                    label=catalog.label_for(name),
                    description=catalog.describe_action(name, args),
                )
                # The terminal 'awaiting_approval' event (with a run_id for
                # resuming) is emitted by the streaming layer, not here.
                approved = yield pend
                if not approved:
                    observation = {"status": "declined",
                                   "message": "The user declined this action, so "
                                              "it was not performed."}
                    self._record(step_no, thought, name, args, observation,
                                 source="—", ok=False, status="declined",
                                 label=catalog.label_for(name),
                                 summary="Declined by user")
                    self._emit({"type": "tool_end", "index": step_no, "tool": name,
                                "ok": False, "status": "declined",
                                "summary": "Declined by user"})
                    continue

            tool_result = mgr.call_tool(name, args, self.context)
            observation = tool_result.content
            status = "done" if tool_result.ok else "error"
            summary = catalog.summarize(name, observation)
            self._record(step_no, thought, name, args, observation,
                         source=tool_result.source, ok=tool_result.ok, status=status,
                         label=catalog.label_for(name), summary=summary)
            self._emit({"type": "tool_end", "index": step_no, "tool": name,
                        "ok": tool_result.ok, "status": status, "summary": summary})

        # Hit the step cap without a FINAL — summarise what we have.
        last = self.result.steps[-1] if self.result.steps else None
        if last is not None:
            self.result.answer = (
                "I reached the step limit before finishing. Here is the most recent "
                f"tool result from `{last.tool}`:\n\n```json\n{_json(last.observation)}\n```"
            )
        else:
            self.result.answer = "I couldn't complete that within the step limit."

    # -- helpers ----------------------------------------------------------- #
    def _record(self, index, thought, tool, args, observation, source, ok,
                status="done", label="", summary="") -> None:
        tag = self._by_name[tool].tag if tool in self._by_name else "read"
        self.result.steps.append(
            Step(index, thought, tool, args, observation, source, ok,
                 label=label, summary=summary, tag=tag, status=status)
        )
        self._transcript.append(_fmt_step(index, thought, tool, args, observation))

    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Prompt building & parsing helpers                                           #
# --------------------------------------------------------------------------- #
def _render_tools(tools: list[ToolInfo]) -> str:
    lines = []
    for t in tools:
        props = (t.input_schema or {}).get("properties", {})
        required = set((t.input_schema or {}).get("required", []))
        params = []
        for pname, pinfo in props.items():
            if pname in catalog.RESERVED_PARAMS:  # injected server-side; hide from LLM
                continue
            typ = pinfo.get("type", "any")
            if pname in required:
                params.append(f"{pname}: {typ}")
            else:
                default = pinfo.get("default", None)
                params.append(f"{pname}: {typ} = {json.dumps(default)}")
        sig = ", ".join(params)
        flag = "  ⚠️ requires approval" if t.tag == "confirm" else ""
        lines.append(f"- {t.name}({sig}) — {t.description}{flag}")
    return "\n".join(lines)


def _build_user_prompt(command: str, tools_desc: str, transcript: list[str]) -> str:
    work = "\n".join(transcript) if transcript else "(none yet)"
    return (
        f"USER COMMAND:\n{command}\n\n"
        f"AVAILABLE TOOLS:\n{tools_desc}\n\n"
        f"WORK SO FAR:\n{work}\n\n"
        "Now produce the next JSON object."
    )


def _fmt_step(idx: int, thought: str, tool: str, args, observation) -> str:
    return (
        f"Step {idx} — thought: {thought}\n"
        f"  action: {tool} {json.dumps(_as_args(args))}\n"
        f"  observation: {_truncate(_json(observation), 1200)}"
    )


def _parse_action(text: str) -> dict | None:
    """Extract the first JSON object from the model's reply, tolerating code
    fences and surrounding prose."""
    if not text:
        return None
    cleaned = text.strip()
    # Strip ```json ... ``` fences if present.
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()

    obj = _try_json_variants(cleaned)
    if obj is not None:
        return obj

    # Fallback: grab the outermost {...} and retry the same variants on it.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = cleaned[start : end + 1]
        return _try_json_variants(snippet)
    return None


def _try_json_variants(snippet: str) -> dict | None:
    """Try parsing as-is, then with escaped quotes/newlines unescaped — some
    models wrap their JSON action in an extra layer of string-escaping."""
    for candidate in (snippet, snippet.replace('\\"', '"').replace("\\n", "\n")):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _as_args(action_input) -> dict:
    if isinstance(action_input, dict):
        return action_input
    return {}


def _as_answer(action_input) -> str:
    if isinstance(action_input, str):
        return action_input.strip()
    if isinstance(action_input, dict) and "answer" in action_input:
        return str(action_input["answer"]).strip()
    return _json(action_input)


def _json(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(obj)


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "\n… (truncated)"
