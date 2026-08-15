"""
brain.py — Nova's orchestrator: a Claude-powered reasoning layer that
decomposes requests and routes them to the right capability.

The whitelist gate lives entirely in actions.py (find_target / execute)
and subagents.py's own folder check. Every tool the orchestrator can call
— open_item, read_document — is wired straight into one of those checks;
there is no path from a model response to raw execution. If no
ANTHROPIC_API_KEY is set, Brain.enabled is False and main.py falls back
to the old pattern-matching "open X" handling, so Nova still works fully
offline.

Sub-agents (currently: the document agent in subagents.py) are plain
Python function calls, not separate processes — each gets its own
narrowly-scoped, tool-less Claude call and zero direct system access.
SessionState (session_state.py) is the shared "world state": persisted
conversation history plus a lightweight task registry the orchestrator
updates before/after delegating to a sub-agent.
"""

import os

import subagents
from actions import execute, find_target
from session_state import SessionState

MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT_TEMPLATE = """You are {agent_name}, a small offline-first desktop assistant running on the user's Windows laptop.

You can hold a normal conversation and answer questions. You have two capabilities beyond talking:
- open_item: open an app or folder the user has pre-approved.
- read_document: read a file inside a pre-approved folder and do something with its contents (summarize it, extract something specific, answer a question about it).

{whitelist_summary}

Rules:
- Only call open_item when the user is clearly asking you to open, launch, or go to something.
- Only call read_document when the user is clearly asking about the contents of a specific file.
- Never claim a tool succeeded unless it reported success.
- If a tool reports the item/file isn't recognized, tell the user plainly — do not guess at a different name or retry with a variant.
- Keep replies short (1-2 sentences) — they may be read aloud by text-to-speech."""

OPEN_ITEM_TOOL = {
    "name": "open_item",
    "description": (
        "Open a pre-approved app or folder by name. Only works for items "
        "already listed in the user's config.json whitelist — cannot open "
        "anything else."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The app or folder name the user asked for, e.g. 'chrome' or 'downloads'.",
            }
        },
        "required": ["name"],
    },
}

READ_DOCUMENT_TOOL = {
    "name": "read_document",
    "description": (
        "Read a file inside one of the user's pre-approved folders and do "
        "something with its contents per the instruction (summarize, "
        "extract specific info, answer a question about it, etc). Only "
        "works for files inside folders already listed in config.json's "
        "whitelist — cannot read files anywhere else. Supports .txt, .md, "
        "and .pdf files."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_name": {
                "type": "string",
                "description": "The file name the user referred to, e.g. 'report.pdf' or 'notes.txt'.",
            },
            "instruction": {
                "type": "string",
                "description": "What to do with the file's contents, e.g. 'summarize this' or 'what is the total on this invoice'.",
            },
        },
        "required": ["file_name", "instruction"],
    },
}

TOOLS = [OPEN_ITEM_TOOL, READ_DOCUMENT_TOOL]

# Single source of truth for which nodes the visual workspace (see
# transcript_window.py) should draw. main.py passes this to
# TranscriptWindow so the window never needs to import brain.py.
AGENTS = [
    ("brain", "Nova"),
    ("app_agent", "Appy"),
    ("document_agent", "Duc"),
]


def _describe_whitelist(config: dict) -> str:
    apps = ", ".join(sorted(config.get("apps", {}).keys())) or "none configured"
    folders = ", ".join(sorted(config.get("folders", {}).keys())) or "none configured"
    return f"Apps you can open: {apps}.\nFolders you can open, or read files from: {folders}."


class Brain:
    def __init__(self, config: dict, on_agent_event=None):
        self.config = config
        self.on_agent_event = on_agent_event or (lambda event: None)
        self.state = SessionState()

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.enabled = bool(api_key) and config.get("use_ai_brain", True)

        if not self.enabled:
            self.client = None
            return

        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.system = SYSTEM_PROMPT_TEMPLATE.format(
            agent_name=config.get("agent_name", "Nova"),
            whitelist_summary=_describe_whitelist(config),
        )

    def respond(self, user_text: str) -> str:
        """Send user_text to Claude, running tools as needed, and return
        the final reply. Conversation history persists across calls
        within a session via self.state.messages. Raises on API/network
        failure — callers should catch and fall back to the offline
        command path."""
        self.state.messages.append({"role": "user", "content": user_text})

        self._emit("brain", "thinking")
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=self.system,
            tools=TOOLS,
            messages=self.state.messages,
        )

        # Bounded loop: caps how many tool round-trips one command can
        # trigger, so a confused model can't loop forever.
        for _ in range(5):
            if response.stop_reason != "tool_use":
                break

            self.state.messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": self._run_tool(block.name, block.input),
                    }
                )
            self.state.messages.append({"role": "user", "content": tool_results})

            self._emit("brain", "thinking")
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=300,
                system=self.system,
                tools=TOOLS,
                messages=self.state.messages,
            )

        self._emit("brain", "idle")
        self.state.messages.append({"role": "assistant", "content": response.content})
        return "".join(block.text for block in response.content if block.type == "text").strip()

    def _emit(self, agent_id: str, status: str, detail: str = ""):
        self.on_agent_event({"agent": agent_id, "status": status, "detail": detail})

    def _run_tool(self, name: str, tool_input: dict) -> str:
        if name == "open_item":
            return self._run_open_item(tool_input.get("name", ""))
        if name == "read_document":
            return self._run_read_document(
                tool_input.get("file_name", ""), tool_input.get("instruction", "")
            )
        return f"Unknown tool: {name}"

    def _run_open_item(self, target_phrase: str) -> str:
        self._emit("app_agent", "running", f"open {target_phrase}")
        kind, name, path = find_target(target_phrase, self.config)
        if not name:
            self._emit("app_agent", "error", f"'{target_phrase}' not in whitelist")
            return f"'{target_phrase}' is not in config.json — cannot open it."
        ok = execute(kind, path)
        if ok:
            self._emit("app_agent", "done", f"opened {name}")
            return f"Opened {name}."
        self._emit("app_agent", "error", f"failed to open {name}")
        return f"Found {name} in config.json but the open call failed. Check its path."

    def _run_read_document(self, file_name: str, instruction: str) -> str:
        task_id = self.state.start_task(f"read {file_name}: {instruction}")
        self._emit("document_agent", "running", f"reading {file_name}")
        try:
            result = subagents.run_document_agent(self.config, self.client, MODEL, instruction, file_name)
            self.state.finish_task(task_id, result=result)
            self._emit("document_agent", "done", f"read {file_name}")
            return result
        except Exception as e:
            error_text = f"The document agent crashed: {e}"
            self.state.finish_task(task_id, error=error_text)
            self._emit("document_agent", "error", str(e))
            return error_text
