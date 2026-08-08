"""
The agent loop.

A small, transparent ReAct-style controller: on each step the model emits one
JSON action — call a tool, or finish — and we execute it, feed the observation
back, and repeat until it produces a FINAL answer or hits the step cap.

Two properties matter beyond the basic loop:

* **Resumable.** When the model chooses an approval-gated tool the run pauses
  (via a generator `yield`) and hands control back to the caller, who approves or
  rejects and resumes the same run. That is what makes `research` a decision the
  user makes rather than something that happens to them.
* **Traceable.** Every step — thought, tool, arguments, observation, and where it
  ran — lands in `AgentResult.steps`, so the UI can show exactly what happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from agent.llm import BaseLLM, LLMError
from agent.parsing import parse_object
from agent.prompts import system_prompt
from config import settings
from tools import catalog
from tools.mcp_client import MCPManager, ToolContext, ToolInfo


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
    """Returned by a run when the model wants to perform an approval-gated action.
    The caller decides, then resumes the run."""

    index: int
    tool: str
    args: dict
    thought: str
    label: str
    description: str


_LLM_UNAVAILABLE = (
    "⚠️ I couldn't reach the language model, so I can't answer that.\n\n"
    "**Reason:** {reason}\n\n"
    "Open **Settings → Model** to add an API key or point Theta at a local Ollama."
)


class Agent:
    def __init__(self, manager: MCPManager, llm: BaseLLM) -> None:
        self.mgr = manager
        self.llm = llm

    def start(
        self,
        command: str,
        context: ToolContext | None = None,
        on_event: Callable[[dict], None] | None = None,
        history: list[dict] | None = None,
    ) -> "AgentRun":
        """Begin a resumable run. Call `.advance()` to drive it: it returns a
        `PendingApproval` when it needs a decision, or an `AgentResult` when done."""
        return AgentRun(self, command, context, on_event, history)

    def run(
        self,
        command: str,
        context: ToolContext | None = None,
        history: list[dict] | None = None,
        auto_approve: bool = False,
    ) -> AgentResult:
        """Convenience driver for non-interactive callers. Approval-gated actions
        are declined unless `auto_approve` is set."""
        run = self.start(command, context, history=history)
        outcome = run.advance()
        while isinstance(outcome, PendingApproval):
            outcome = run.advance(approved=auto_approve)
        return outcome


class AgentRun:
    """One resumable execution of the agent loop."""

    def __init__(
        self,
        agent: Agent,
        command: str,
        context: ToolContext | None = None,
        on_event: Callable[[dict], None] | None = None,
        history: list[dict] | None = None,
        max_steps: int | None = None,
    ) -> None:
        self.agent = agent
        self.command = (command or "").strip()
        self.context = context
        self.on_event = on_event
        self.history = history or []
        self.max_steps = max_steps or settings.max_agent_steps
        self.result = AgentResult(
            answer="", llm_label=agent.llm.label, transport=agent.mgr.transport
        )
        self.pending: PendingApproval | None = None
        self.finished = False
        self._started = False

        tools = agent.mgr.list_tools()
        self._tools_desc = _render_tools(tools)
        self._by_name = {t.name: t for t in tools}
        # Which page elements will pause for approval, from the latest observation.
        self._gates: dict = {}
        self._gen = self._iter()

        # Let tools stream their own progress into this run's event channel.
        if context is not None and context.on_progress is None:
            context.on_progress = self._emit

    # -- driving ----------------------------------------------------------- #
    def advance(self, approved: bool | None = None, args: dict | None = None):
        """Run until the next approval pause or completion.

        `args` lets the approver hand back *edited* arguments — the user can
        rewrite the research plan on the approval card before starting it.
        """
        if self.finished:
            return self.result
        try:
            decision = None if not self._started else {"approved": approved, "args": args}
            self._started = True
            self.pending = self._gen.send(decision)
            return self.pending
        except StopIteration:
            self.finished = True
            self.pending = None
            return self.result

    # -- the loop (generator; yields at approval points) ------------------- #
    def _iter(self):
        if not self.command:
            self.result.answer = "Ask me a question and I'll research it."
            return

        llm, mgr = self.agent.llm, self.agent.mgr

        for step_no in range(1, self.max_steps + 1):
            prompt = _build_user_prompt(
                self.command, self._tools_desc, self.result.steps, self.history
            )
            try:
                raw = llm.complete(system_prompt(), prompt)
            except LLMError as ex:
                self.result.error = str(ex)
                self.result.answer = _LLM_UNAVAILABLE.format(reason=ex)
                return

            action = parse_object(raw)
            if action is None:  # prose, not JSON → treat it as the final answer
                self.result.answer = raw.strip() or "(the model returned an empty response)"
                return

            thought = str(action.get("thought", "")).strip()
            name = str(action.get("action", "")).strip()
            action_input = action.get("action_input", {})

            if name.upper() == "FINAL" or not name:
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

            # Approval gate. For browser actions this is decided from the live
            # page: the same tool is safe on a search button and consequential on
            # "Place order", so the verdict comes from the last observation.
            level, why = catalog.risk(name, args, self._gates)
            if level == catalog.CONFIRM:
                pend = PendingApproval(
                    index=step_no, tool=name, args=args, thought=thought,
                    label=catalog.label_for(name),
                    description=why or catalog.describe_action(name, args),
                )
                # The terminal 'awaiting_approval' event (carrying a run_id) is
                # emitted by the streaming layer, not here.
                decision = yield pend
                approved, args = _decision(decision, args, _editable(tool))
                if not approved:
                    observation = {
                        "status": "declined",
                        "message": "The user declined this action, so it was not performed.",
                    }
                    self._record(step_no, thought, name, args, observation, source="—",
                                 ok=False, status="declined",
                                 label=catalog.label_for(name), summary="Declined")
                    self._emit({"type": "tool_end", "index": step_no, "tool": name,
                                "label": catalog.label_for(name), "args": args,
                                "ok": False, "status": "declined", "summary": "Declined"})
                    continue

            tool_result = mgr.call_tool(name, args, self.context)
            observation = tool_result.content
            # Refresh the approval gates from this observation, so the next
            # proposed click is judged against the page as it is *now*.
            if isinstance(observation, dict) and "gates" in observation:
                self._gates = observation.get("gates") or {}
            status = "done" if tool_result.ok else "error"
            summary = catalog.summarize(name, observation)
            self._record(step_no, thought, name, args, observation,
                         source=tool_result.source, ok=tool_result.ok, status=status,
                         label=catalog.label_for(name), summary=summary)
            # The event carries everything the run recorder and the live view need,
            # so neither has to reach back into the observation.
            event = {"type": "tool_end", "index": step_no, "tool": name,
                     "label": catalog.label_for(name), "args": args,
                     "ok": tool_result.ok, "status": status, "summary": summary}
            if isinstance(observation, dict):
                event["url"] = observation.get("url", "")
                event["screenshot"] = observation.get("screenshot", "")
                if observation.get("target"):
                    event["target"] = observation["target"]
                if observation.get("warnings"):
                    event["warnings"] = observation["warnings"]
                if observation.get("path"):          # file_write
                    event["output"] = observation["path"]
            self._emit(event)

        # Hit the step cap without a FINAL — say so honestly.
        last = self.result.steps[-1] if self.result.steps else None
        if last is not None:
            self.result.answer = (
                f"I reached the {self.max_steps}-step limit before finishing. "
                f"The last thing I did was `{last.tool}` — {last.summary}. "
                "Tell me what to do next, or break the task into smaller steps."
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

    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Prompt building                                                             #
# --------------------------------------------------------------------------- #
def _render_tools(tools: list[ToolInfo]) -> str:
    """Group tools by server so the model sees capabilities, not a flat list."""
    order = ["browser", "notion", "gmail", "workspace", "web", "briefs", "theta"]
    groups: dict[str, list[str]] = {}
    for t in tools:
        if t.name in catalog.HIDDEN_TOOLS:
            continue
        props = (t.input_schema or {}).get("properties", {})
        required = set((t.input_schema or {}).get("required", []))
        params = []
        for pname, pinfo in props.items():
            if pname in catalog.RESERVED_PARAMS:  # injected server-side; hide it
                continue
            typ = pinfo.get("type", "any")
            if pname in required:
                params.append(f"{pname}: {typ}")
            else:
                params.append(f"{pname}: {typ} = {json.dumps(pinfo.get('default'))}")
        flag = "  ⚠️ pauses for the user" if t.tag == "confirm" else ""
        groups.setdefault(t.server, []).append(
            f"- {t.name}({', '.join(params)}) — {t.description}{flag}"
        )

    lines = []
    for server in order + [s for s in groups if s not in order]:
        if server in groups:
            lines.append(f"[{server}]")
            lines += groups[server]
    return "\n".join(lines)


def _build_user_prompt(
    command: str, tools_desc: str, steps: list[Step], history: list[dict]
) -> str:
    parts = []
    if history:
        turns = "\n".join(
            f"{h.get('role', 'user')}: {_truncate(str(h.get('content', '')), 600)}"
            for h in history
        )
        parts.append(f"CONVERSATION SO FAR:\n{turns}")
    parts.append(f"GOAL:\n{command}")
    parts.append(f"AVAILABLE TOOLS:\n{tools_desc}")
    parts.append("WORK SO FAR:\n" + (_render_steps(steps) if steps else "(nothing yet)"))
    parts.append(
        "Now produce the next JSON object. Use only refs from the CURRENT PAGE above."
    )
    return "\n\n".join(parts)


def _render_steps(steps: list[Step]) -> str:
    """Replay the run for the model, keeping only the *latest* page in full.

    A page observation is a few thousand tokens. Carrying every one of them
    through a twenty-step browser task would blow the context window and cost a
    fortune, and stale element refs actively mislead the model — they are
    renumbered after every action. So history collapses to what was done and
    what came of it, and only the current page is described in full.
    """
    lines: list[str] = []
    for step in steps[:-1]:
        lines.append(
            f"Step {step.index}: {step.tool}"
            f"{_compact_args(step.args)} → {step.summary or ('ok' if step.ok else 'failed')}"
        )

    last = steps[-1]
    lines.append(
        f"Step {last.index}: {last.tool}{_compact_args(last.args)}"
        f" → {last.summary or ('ok' if last.ok else 'failed')}"
    )
    observation = last.observation
    if isinstance(observation, dict) and observation.get("page"):
        detail = {k: v for k, v in observation.items()
                  if k not in ("page", "gates", "target", "screenshot")}
        if detail:
            lines.append(_truncate(_json(detail), 900))
        lines.append("\nCURRENT PAGE:\n" + str(observation["page"]))
    else:
        lines.append(_truncate(_json(observation), 3000))
    return "\n".join(lines)


def _compact_args(args) -> str:
    args = _as_args(args)
    shown = {k: v for k, v in args.items() if k not in catalog.RESERVED_PARAMS}
    if not shown:
        return ""
    return "(" + ", ".join(f"{k}={_truncate(str(v), 60)!r}" for k, v in shown.items()) + ")"


def _editable(tool: ToolInfo) -> set[str]:
    """Parameters an approver may rewrite: everything the tool declares except
    the reserved ones the manager injects."""
    props = (tool.input_schema or {}).get("properties", {})
    return set(props) - catalog.RESERVED_PARAMS


def _decision(decision, args: dict, allowed: set[str]) -> tuple[bool, dict]:
    """Interpret what came back through the generator at an approval point.

    A bare bool is the simple yes/no. A dict may also carry replacement `args`,
    which is how an edited research plan gets back into the run. Edits are
    restricted to parameters the tool actually declares (minus the reserved ones
    injected server-side), so approving can never smuggle in an argument the tool
    was not designed to take.
    """
    if not isinstance(decision, dict):
        return bool(decision), args
    approved = bool(decision.get("approved"))
    edited = decision.get("args")
    if approved and isinstance(edited, dict):
        args = {**args, **{k: v for k, v in edited.items() if k in allowed}}
    return approved, args


def _as_args(action_input) -> dict:
    return action_input if isinstance(action_input, dict) else {}


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
