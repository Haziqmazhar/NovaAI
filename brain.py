"""
brain.py — Nova's orchestrator: a GPT-powered reasoning layer that
decomposes requests and routes them to the right capability.

The whitelist gate lives entirely in actions.py (find_target / execute)
and subagents.py's own folder check. Every tool the orchestrator can call
— open_item, read_document — is wired straight into one of those checks;
there is no path from a model response to raw execution. If no
OPENAI_API_KEY is set, Brain.enabled is False and main.py falls back to
the old pattern-matching "open X" handling, so Nova still works fully
offline.

Sub-agents (currently: the document agent in subagents.py) are plain
Python function calls, not separate processes — each gets its own
narrowly-scoped, tool-less GPT call and zero direct system access.
SessionState (session_state.py) is the shared "world state": persisted
conversation history plus a lightweight task registry the orchestrator
updates before/after delegating to a sub-agent.

Uses OpenAI's Responses API (client.responses.create), the current
recommended surface for tool use — not the older Chat Completions API.
"""

import json
import os
import threading
from datetime import datetime

import coding_agent
import discovery
import memory
import subagents
from actions import execute, find_target
from session_state import SessionState

MODEL = "gpt-5-mini"
MAX_REMINDER_DELAY_SECONDS = 7 * 24 * 3600  # 1 week safety cap

SYSTEM_PROMPT_TEMPLATE = """You are {agent_name}, a small offline-first desktop assistant running on the user's Windows laptop.

Current local time: {current_time}

You can hold a normal conversation and answer questions. You have seven capabilities beyond talking:
- open_item: open an app or folder the user has pre-approved.
- list_documents: list the readable files (.txt, .md, .pdf, .docx, .csv, .xlsx) inside a pre-approved folder, or across all of them.
- read_document: read one or more files inside pre-approved folders and do something with their contents (summarize, extract something specific, answer a question, compare multiple files).
- download_file: download a file from a URL and save it into one pre-approved folder.
- launch_coding_agent: open Claude Code, a separate full coding agent, in a new window scoped to one pre-approved repo.
- set_reminder: schedule a spoken/logged reminder to fire later in this same session.
- web_search: look up current information on the open web.

{whitelist_summary}

Rules:
- Only call open_item when the user is clearly asking you to open, launch, or go to something.
- If the user asks about documents/files without naming an exact filename (e.g. "what's in my downloads", "find the budget file"), call list_documents first instead of guessing a filename.
- Only call read_document when the user is clearly asking about the contents of specific file(s) — pass every file they name in one call when they want them compared or considered together.
- Only call download_file when the user gives an explicit URL and asks to save/download it. It always needs a specific pre-approved folder to save into — if they haven't said which one, ask them rather than guessing.
- Only call launch_coding_agent when the user explicitly asks to launch/start/open Claude Code or a coding agent on a named repo. You only start that session — you cannot see or control what happens inside it, so never claim credit for anything it does; just confirm the window opened.
- Only call set_reminder when the user is clearly asking to be reminded/alerted/notified later. Use the current local time above to compute delay_seconds — e.g. if it's 14:00 and they say "remind me at 2:30", that's delay_seconds=1800.
- Only call web_search when answering requires current or specific information you don't already know — don't search for things you can answer directly.
- Never claim a tool succeeded unless it reported success.
- If a tool reports the item/file isn't recognized, tell the user plainly — do not guess at a different name or retry with a variant.
- Keep replies short (1-2 sentences) — they may be read aloud by text-to-speech."""

OPEN_ITEM_TOOL = {
    "type": "function",
    "name": "open_item",
    "description": (
        "Open a pre-approved app or folder by name. Only works for items "
        "already listed in the user's config.json whitelist — cannot open "
        "anything else."
    ),
    "parameters": {
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
    "type": "function",
    "name": "read_document",
    "description": (
        "Read one or more files inside the user's pre-approved folders and "
        "do something with their contents per the instruction (summarize, "
        "extract specific info, answer a question, compare/cross-reference "
        "multiple files). Only works for files inside folders already "
        "listed in config.json's whitelist — cannot read files anywhere "
        "else. Supports .txt, .md, .pdf, .docx, .csv, and .xlsx files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_names": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "The file name(s) the user referred to, e.g. ['report.pdf'] or ['invoice_march.pdf', 'invoice_april.pdf'] to compare them.",
            },
            "instruction": {
                "type": "string",
                "description": "What to do with the file contents, e.g. 'summarize this' or 'compare the totals on these two invoices'.",
            },
        },
        "required": ["file_names", "instruction"],
    },
}

LIST_DOCUMENTS_TOOL = {
    "type": "function",
    "name": "list_documents",
    "description": (
        "List the readable files (.txt, .md, .pdf, .docx, .csv, .xlsx) "
        "inside one of the user's pre-approved folders, or across all of "
        "them if no folder is given. Use this to discover what files "
        "exist before calling read_document, e.g. when the user doesn't "
        "name an exact filename."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "folder_name": {
                "type": "string",
                "description": "Optional whitelisted folder name, e.g. 'downloads'. Omit to list across all whitelisted folders.",
            }
        },
        "required": [],
    },
}

SET_REMINDER_TOOL = {
    "type": "function",
    "name": "set_reminder",
    "description": (
        "Schedule a reminder to be spoken/logged after a delay, within "
        "this same running session. Not persisted across restarts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "What to remind the user about, e.g. 'check the oven'.",
            },
            "delay_seconds": {
                "type": "integer",
                "description": (
                    "Seconds from now to deliver the reminder — compute this "
                    "from the current local time given in your instructions "
                    "and what the user asked for."
                ),
            },
        },
        "required": ["message", "delay_seconds"],
    },
}

DOWNLOAD_FILE_TOOL = {
    "type": "function",
    "name": "download_file",
    "description": (
        "Download a file from a URL and save it into one of the user's "
        "pre-approved folders. Only http/https URLs are allowed; "
        "executable/script files are refused; an existing file with the "
        "same name is never overwritten (a numbered suffix is used "
        "instead). Always requires a specific whitelisted folder_name — "
        "if the user hasn't said which folder, ask them rather than "
        "guessing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The http(s) URL to download.",
            },
            "folder_name": {
                "type": "string",
                "description": "Which pre-approved folder to save into, e.g. 'downloads'.",
            },
            "file_name": {
                "type": "string",
                "description": "Optional file name to save as. If omitted, it's derived from the URL.",
            },
        },
        "required": ["url", "folder_name"],
    },
}

# Hosted tool — OpenAI runs the search itself; Nova never invokes this
# directly the way it invokes the function tools above (see Brain.respond).
WEB_SEARCH_TOOL = {"type": "web_search"}

# Only offered to the model when config.json's "memory_enabled" is true
# (see Brain.__init__) — off by default, so this is an explicit opt-in the
# same way voice_enabled/tts_enabled are, since it's a write capability.
REMEMBER_TOOL = {
    "type": "function",
    "name": "remember",
    "description": (
        "Save a short, durable note about the user for future sessions — a "
        "preference, a fact, ongoing context. Appends to Nova's memory file; "
        "never overwrites or deletes anything already remembered. Only call "
        "this for things that are true and worth keeping long-term, not "
        "routine commands or one-off actions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "The fact/preference to remember, e.g. 'Prefers dark roast coffee' or \"Dog's name is Max\".",
            }
        },
        "required": ["note"],
    },
}

# The next five are only offered to the model when config.json's
# "discord_enabled" is true and a real DiscordAgent is wired up (see
# Brain.__init__) — same explicit-opt-in treatment as REMEMBER_TOOL. Every
# one of them acts only inside the one whitelisted Discord server
# (discord_agent.py enforces this, not the model).
DISCORD_SEND_MESSAGE_TOOL = {
    "type": "function",
    "name": "discord_send_message",
    "description": "Send a message to a channel in the whitelisted Discord server.",
    "parameters": {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "The channel name, e.g. 'general'."},
            "text": {"type": "string", "description": "The message to send."},
        },
        "required": ["channel_name", "text"],
    },
}

DISCORD_CREATE_CHANNEL_TOOL = {
    "type": "function",
    "name": "discord_create_channel",
    "description": "Create a new text channel in the whitelisted Discord server.",
    "parameters": {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Name for the new channel."},
        },
        "required": ["channel_name"],
    },
}

DISCORD_DELETE_CHANNEL_TOOL = {
    "type": "function",
    "name": "discord_delete_channel",
    "description": "Delete a text channel in the whitelisted Discord server. This can't be undone.",
    "parameters": {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Name of the channel to delete."},
        },
        "required": ["channel_name"],
    },
}

DISCORD_RENAME_CHANNEL_TOOL = {
    "type": "function",
    "name": "discord_rename_channel",
    "description": "Rename a text channel in the whitelisted Discord server.",
    "parameters": {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Current channel name."},
            "new_name": {"type": "string", "description": "New name for the channel."},
        },
        "required": ["channel_name", "new_name"],
    },
}

DISCORD_DELETE_LAST_BOT_MESSAGE_TOOL = {
    "type": "function",
    "name": "discord_delete_last_bot_message",
    "description": (
        "Delete Nova's own most recent message in a Discord channel. Only "
        "ever deletes a message Nova itself sent — never another user's."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Channel to delete Nova's last message from."},
        },
        "required": ["channel_name"],
    },
}

LAUNCH_CODING_AGENT_TOOL = {
    "type": "function",
    "name": "launch_coding_agent",
    "description": (
        "Open Claude Code — a separate, fully capable AI coding agent — in "
        "a new terminal window, scoped to one of the user's pre-approved "
        "repos. Nova does not do any coding itself and cannot see or "
        "control what happens in that session once it's open; it only "
        "starts it. Only call this when the user explicitly asks to "
        "launch/start/open Claude Code or a coding agent on a named repo."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo_name": {
                "type": "string",
                "description": "Which pre-approved repo to open, e.g. 'nova'.",
            }
        },
        "required": ["repo_name"],
    },
}

TOOLS = [
    OPEN_ITEM_TOOL,
    READ_DOCUMENT_TOOL,
    LIST_DOCUMENTS_TOOL,
    DOWNLOAD_FILE_TOOL,
    LAUNCH_CODING_AGENT_TOOL,
    SET_REMINDER_TOOL,
    WEB_SEARCH_TOOL,
]

# Single source of truth for which nodes the visual workspace (see
# transcript_window.py) should draw. main.py passes this to
# TranscriptWindow so the window never needs to import brain.py.
AGENTS = [
    ("brain", "Nova", "#b46bff"),
    ("app_agent", "Appy", "#4aa3ff"),
    ("document_agent", "Duc", "#3ddc84"),
    ("reminder_agent", "Remy", "#ffb454"),
    ("lookup_agent", "Scout", "#3dd6dc"),
    ("coding_agent", "Cody", "#ff5f7e"),
    ("discord_agent", "Herald", "#f4c94c"),
]


def _describe_whitelist(config: dict) -> str:
    apps = ", ".join(sorted(config.get("apps", {}).keys())) or "none configured"
    folders = ", ".join(sorted(config.get("folders", {}).keys())) or "none configured"
    repos = ", ".join(sorted(config.get("repos", {}).keys())) or "none configured"
    return (
        f"Apps you can open: {apps}.\n"
        f"Folders you can open, or read files from: {folders}.\n"
        f"Repos you can open Claude Code in: {repos}."
    )


class Brain:
    def __init__(self, config: dict, on_agent_event=None, on_reminder=None):
        self.config = config
        self.on_agent_event = on_agent_event or (lambda event: None)
        self.on_reminder = on_reminder or (lambda message: None)
        self.state = SessionState()
        # Wired externally by main.py once a DiscordAgent has been built —
        # None until then, and stays None entirely if discord_enabled is
        # false. Tool *availability* below only checks config, not this
        # attribute, since main.py constructs Brain before DiscordAgent
        # (which itself needs a Brain instance to hand messages to).
        self.discord_agent = None

        api_key = os.environ.get("OPENAI_API_KEY")
        self.enabled = bool(api_key) and config.get("use_ai_brain", True)

        self.tools = list(TOOLS)
        if config.get("memory_enabled", False):
            self.tools.append(REMEMBER_TOOL)
        if config.get("discord_enabled", False):
            self.tools += [
                DISCORD_SEND_MESSAGE_TOOL,
                DISCORD_CREATE_CHANNEL_TOOL,
                DISCORD_DELETE_CHANNEL_TOOL,
                DISCORD_RENAME_CHANNEL_TOOL,
                DISCORD_DELETE_LAST_BOT_MESSAGE_TOOL,
            ]

        if not self.enabled:
            self.client = None
            return

        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)

    def _build_system_prompt(self) -> str:
        # Built fresh per call (not cached at __init__) so a long-running
        # session doesn't hand the model a stale "current time" — this is
        # what set_reminder relies on to turn "at 5pm" into delay_seconds.
        prompt = SYSTEM_PROMPT_TEMPLATE.format(
            agent_name=self.config.get("agent_name", "Nova"),
            whitelist_summary=_describe_whitelist(self.config),
            current_time=datetime.now().strftime("%A %Y-%m-%d %H:%M"),
        )
        if self.config.get("memory_enabled", False):
            prompt += "\n\n" + self._memory_block()
        if self.config.get("discord_enabled", False):
            prompt += "\n\n" + self._discord_block()
        return prompt

    def _memory_block(self) -> str:
        return (
            "You also have a seventh capability: remember — save a short, "
            "durable fact about the user for future sessions. Only call it "
            "for things that are true and worth keeping long-term, not "
            "routine commands.\n\n"
            f"What you already remember about the user:\n{memory.read_memory(self.config)}"
        )

    def _discord_block(self) -> str:
        return (
            "You also have Discord server-management capabilities: "
            "discord_send_message, discord_create_channel, discord_delete_channel, "
            "discord_rename_channel, discord_delete_last_bot_message. These only "
            "ever act inside the one Discord server the user has whitelisted — "
            "there is no way to target any other server. Channels are targeted "
            "by name; if a tool reports a channel doesn't exist, tell the user "
            "plainly rather than guessing a different name. Deleting a channel "
            "or a message cannot be undone, so only do it when the user is "
            "clearly asking for that specific action — never as a guess."
        )

    def respond(self, user_text: str) -> str:
        """Send user_text to GPT, running tools as needed, and return
        the final reply. Conversation history persists across calls
        within a session via self.state.messages. Raises on API/network
        failure — callers should catch and fall back to the offline
        command path."""
        self.state.messages.append({"role": "user", "content": user_text})
        system = self._build_system_prompt()

        self._emit("brain", "thinking")
        response = self.client.responses.create(
            model=MODEL,
            instructions=system,
            tools=self.tools,
            input=self.state.messages,
        )
        self._emit_web_search_events(response)

        # Bounded loop: caps how many tool round-trips one command can
        # trigger, so a confused model can't loop forever.
        for _ in range(5):
            self.state.messages += response.output
            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                break

            for call in function_calls:
                tool_input = json.loads(call.arguments)
                self.state.messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": self._run_tool(call.name, tool_input),
                    }
                )

            self._emit("brain", "thinking")
            response = self.client.responses.create(
                model=MODEL,
                instructions=system,
                tools=self.tools,
                input=self.state.messages,
            )
            self._emit_web_search_events(response)

        self._emit("brain", "idle")
        return (response.output_text or "").strip()

    def _emit(self, agent_id: str, status: str, detail: str = ""):
        self.on_agent_event({"agent": agent_id, "status": status, "detail": detail})

    def _emit_web_search_events(self, response):
        # web_search is a hosted tool — OpenAI runs it inside the same
        # responses.create() call, so Nova never "invokes" it the way it
        # invokes function tools and can't bracket it with running/done
        # beforehand. Best-effort: report it to the dashboard after the
        # fact by scanning for web_search_call items in the output.
        for item in response.output:
            if getattr(item, "type", None) != "web_search_call":
                continue
            action = getattr(item, "action", None)
            query = getattr(action, "query", None) or "web search"
            self._emit("lookup_agent", "running", query)
            self._emit("lookup_agent", "done", query)

    def _run_tool(self, name: str, tool_input: dict) -> str:
        if name == "open_item":
            return self._run_open_item(tool_input.get("name", ""))
        if name == "read_document":
            return self._run_read_document(
                tool_input.get("file_names", []), tool_input.get("instruction", "")
            )
        if name == "list_documents":
            return self._run_list_documents(tool_input.get("folder_name"))
        if name == "download_file":
            return self._run_download_file(
                tool_input.get("url", ""),
                tool_input.get("folder_name"),
                tool_input.get("file_name"),
            )
        if name == "launch_coding_agent":
            return self._run_launch_coding_agent(tool_input.get("repo_name", ""))
        if name == "set_reminder":
            return self._run_set_reminder(
                tool_input.get("message", ""), tool_input.get("delay_seconds")
            )
        if name == "remember":
            return self._run_remember(tool_input.get("note", ""))
        if name == "discord_send_message":
            return self._run_discord(
                "send_message", tool_input.get("channel_name", ""), tool_input.get("text", "")
            )
        if name == "discord_create_channel":
            return self._run_discord("create_channel", tool_input.get("channel_name", ""))
        if name == "discord_delete_channel":
            return self._run_discord("delete_channel", tool_input.get("channel_name", ""))
        if name == "discord_rename_channel":
            return self._run_discord(
                "rename_channel", tool_input.get("channel_name", ""), tool_input.get("new_name", "")
            )
        if name == "discord_delete_last_bot_message":
            return self._run_discord("delete_last_bot_message", tool_input.get("channel_name", ""))
        return f"Unknown tool: {name}"

    def _run_open_item(self, target_phrase: str) -> str:
        self._emit("app_agent", "running", f"open {target_phrase}")
        kind, name, path = find_target(target_phrase, self.config)
        if not name:
            self._emit("app_agent", "error", f"'{target_phrase}' not in whitelist")
            found = discovery.find_installed_app(self.config, target_phrase)
            if found:
                return (
                    f"'{target_phrase}' isn't in your whitelist yet, but I found "
                    f"'{found['name']}' installed at {found['path']}. Run "
                    f"'python discover_apps.py' to add it, or add it to "
                    f"config.json yourself — I can't add it for you."
                )
            return f"'{target_phrase}' is not in config.json — cannot open it."
        ok = execute(kind, path)
        if ok:
            self._emit("app_agent", "done", f"opened {name}")
            return f"Opened {name}."
        self._emit("app_agent", "error", f"failed to open {name}")
        return f"Found {name} in config.json but the open call failed. Check its path."

    def _run_read_document(self, file_names: list, instruction: str) -> str:
        names_desc = ", ".join(file_names) or "(no files given)"
        task_id = self.state.start_task(f"read {names_desc}: {instruction}")
        self._emit("document_agent", "running", f"reading {names_desc}")
        try:
            result = subagents.run_document_agent(self.config, self.client, MODEL, instruction, file_names)
            self.state.finish_task(task_id, result=result)
            self._emit("document_agent", "done", f"read {names_desc}")
            return result
        except Exception as e:
            error_text = f"The document agent crashed: {e}"
            self.state.finish_task(task_id, error=error_text)
            self._emit("document_agent", "error", str(e))
            return error_text

    def _run_list_documents(self, folder_name: str = None) -> str:
        desc = f"folder {folder_name}" if folder_name else "all folders"
        task_id = self.state.start_task(f"list documents in {desc}")
        self._emit("document_agent", "running", f"listing {desc}")
        try:
            result = subagents.list_documents(self.config, folder_name)
            self.state.finish_task(task_id, result=result)
            self._emit("document_agent", "done", f"listed {desc}")
            return result
        except Exception as e:
            error_text = f"The document agent crashed while listing files: {e}"
            self.state.finish_task(task_id, error=error_text)
            self._emit("document_agent", "error", str(e))
            return error_text

    def _run_download_file(self, url: str, folder_name: str = None, file_name: str = None) -> str:
        task_id = self.state.start_task(f"download {url} -> {folder_name or '?'}")
        self._emit("document_agent", "running", f"downloading {url}")
        try:
            result = subagents.download_file(self.config, url, folder_name, file_name)
            self.state.finish_task(task_id, result=result)
            self._emit("document_agent", "done", f"downloaded {url}")
            return result
        except Exception as e:
            error_text = f"The document agent crashed while downloading: {e}"
            self.state.finish_task(task_id, error=error_text)
            self._emit("document_agent", "error", str(e))
            return error_text

    def _run_launch_coding_agent(self, repo_name: str) -> str:
        task_id = self.state.start_task(f"launch coding agent: {repo_name}")
        self._emit("coding_agent", "running", f"opening {repo_name}")
        try:
            result = coding_agent.launch_coding_agent(self.config, repo_name)
            self.state.finish_task(task_id, result=result)
            self._emit("coding_agent", "done", repo_name)
            return result
        except Exception as e:
            error_text = f"Launching the coding agent crashed: {e}"
            self.state.finish_task(task_id, error=error_text)
            self._emit("coding_agent", "error", str(e))
            return error_text

    def _run_set_reminder(self, message: str, delay_seconds) -> str:
        try:
            delay_seconds = _validate_delay(delay_seconds)
        except ValueError as e:
            return str(e)

        task_id = self.state.start_task(f"reminder in {_humanize_seconds(delay_seconds)}: {message}")
        self._emit("reminder_agent", "running", f"in {_humanize_seconds(delay_seconds)}: {message}")
        timer = threading.Timer(delay_seconds, self._fire_reminder, args=(task_id, message))
        timer.daemon = True
        timer.start()
        self._emit("reminder_agent", "done", "scheduled")
        return f"Reminder set for {_humanize_seconds(delay_seconds)} from now: '{message}'."

    def _fire_reminder(self, task_id: str, message: str):
        # Runs on the Timer's own background thread, independent of any
        # in-flight respond() call. Calling self.on_reminder / self._emit
        # from a background thread is safe — see transcript_window.py's
        # evaluate_js() note, which this ultimately calls into.
        self._emit("reminder_agent", "running", "delivering")
        self.state.finish_task(task_id, result=message)
        self.on_reminder(message)
        self._emit("reminder_agent", "done", "delivered")

    def _run_remember(self, note: str) -> str:
        # No dedicated agent/room for this — recalling what Nova knows is
        # folded into every turn the same silent way short-term
        # conversation history already is; it isn't a delegated task the
        # way opening an app or searching the web is.
        note = (note or "").strip()
        if not note:
            return "No note given — nothing to remember."
        try:
            memory.append_memory(self.config, note)
        except Exception as e:
            return f"Couldn't save that to memory: {e}"
        return f"Got it — I'll remember: {note}"

    def _run_discord(self, action: str, *args) -> str:
        # All five discord_* tools share identical shape (check the agent
        # exists, emit running, call the matching DiscordAgent method, emit
        # done/error) — one parameterized handler instead of five
        # copy-pasted ones, dispatching by method name on self.discord_agent.
        if not self.discord_agent:
            return "Discord isn't connected — check discord_enabled and DISCORD_BOT_TOKEN."
        desc = (action + " " + " ".join(str(a) for a in args if a)).strip()
        task_id = self.state.start_task(f"discord: {desc}")
        self._emit("discord_agent", "running", desc)
        try:
            result = getattr(self.discord_agent, action)(*args)
            self.state.finish_task(task_id, result=result)
            self._emit("discord_agent", "done", desc)
            return result
        except Exception as e:
            error_text = f"Discord action crashed: {e}"
            self.state.finish_task(task_id, error=error_text)
            self._emit("discord_agent", "error", str(e))
            return error_text


def _validate_delay(delay_seconds) -> int:
    """Coerce/clamp a raw delay_seconds tool argument into a valid positive
    int, or raise ValueError with a user-facing message. Pure and
    side-effect-free — the piece of set_reminder worth testing without a
    running Brain/API key (see test_reminder_logic.py)."""
    try:
        delay_seconds = int(delay_seconds)
    except (TypeError, ValueError):
        raise ValueError("Couldn't schedule that reminder — no valid delay was given.")
    if delay_seconds < 1:
        raise ValueError("Reminder delay must be at least 1 second in the future.")
    return min(delay_seconds, MAX_REMINDER_DELAY_SECONDS)


def _humanize_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m" + (f" {secs}s" if secs else "")
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" + (f" {minutes}m" if minutes else "")
    days, hours = divmod(hours, 24)
    return f"{days}d" + (f" {hours}h" if hours else "")
