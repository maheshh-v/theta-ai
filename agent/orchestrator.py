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

from agent.llm import BaseLLM, LLMError, build_llm
from config import settings
from tools.mcp_client import MCPManager, ToolInfo

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


@dataclass
class AgentResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    llm_label: str = ""
    transport: str = ""
    error: str | None = None


class Agent:
    def __init__(self, manager: MCPManager, llm: BaseLLM | None = None) -> None:
        self.mgr = manager
        self.llm = llm or build_llm()

    # ------------------------------------------------------------------ #
    def run(self, command: str) -> AgentResult:
        command = (command or "").strip()
        result = AgentResult(
            answer="", llm_label=self.llm.label, transport=self.mgr.transport
        )
        if not command:
            result.answer = "Please type a command, e.g. “what's on my calendar?”"
            return result

        tools = self.mgr.list_tools()
        tools_desc = _render_tools(tools)
        valid = {t.name for t in tools}
        transcript: list[str] = []

        for step_no in range(1, settings.max_agent_steps + 1):
            user_prompt = _build_user_prompt(command, tools_desc, transcript)

            try:
                raw = self.llm.complete(SYSTEM_PROMPT, user_prompt)
            except LLMError as ex:
                result.error = str(ex)
                result.answer = (
                    "⚠️ The language model is unavailable, so I couldn't process that.\n\n"
                    f"Reason: {ex}\n\n"
                    "Tip: set a free GEMINI_API_KEY in .env, run a local Ollama, "
                    "or leave both unset to use the built-in mock mode."
                )
                return result

            action = _parse_action(raw)

            # No parseable JSON → treat the model's prose as its final answer.
            if action is None:
                result.answer = raw.strip() or "(the model returned an empty response)"
                return result

            thought = str(action.get("thought", "")).strip()
            name = str(action.get("action", "")).strip()
            action_input = action.get("action_input", {})

            if name.upper() == "FINAL" or name == "":
                result.answer = _as_answer(action_input)
                if not result.steps and not result.answer:
                    result.answer = "(no answer produced)"
                return result

            # It's a tool call.
            if name not in valid:
                observation = {
                    "error": f"'{name}' is not an available tool. "
                    f"Choose from: {', '.join(sorted(valid))}."
                }
                result.steps.append(
                    Step(step_no, thought, name, _as_args(action_input),
                         observation, source="—", ok=False)
                )
                transcript.append(
                    _fmt_step(step_no, thought, name, action_input, observation)
                )
                continue

            args = _as_args(action_input)
            tool_result = self.mgr.call_tool(name, args)
            observation = tool_result.content
            result.steps.append(
                Step(step_no, thought, name, args, observation,
                     source=tool_result.source, ok=tool_result.ok)
            )
            transcript.append(_fmt_step(step_no, thought, name, args, observation))

        # Hit the step cap without a FINAL — summarise what we have.
        last = result.steps[-1] if result.steps else None
        if last is not None:
            result.answer = (
                "I reached the step limit before finishing. Here is the most recent "
                f"tool result from `{last.tool}`:\n\n```json\n"
                f"{_json(last.observation)}\n```"
            )
        else:
            result.answer = "I couldn't complete that within the step limit."
        return result


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
            typ = pinfo.get("type", "any")
            if pname in required:
                params.append(f"{pname}: {typ}")
            else:
                default = pinfo.get("default", None)
                params.append(f"{pname}: {typ} = {json.dumps(default)}")
        sig = ", ".join(params)
        lines.append(f"- {t.name}({sig}) — {t.description}")
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

    # Fast path.
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Fallback: grab the outermost {...}.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = cleaned[start : end + 1]
        try:
            obj = json.loads(snippet)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return None
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
