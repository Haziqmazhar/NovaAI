"""
brain.py — optional Claude-powered reasoning layer for Nova.

The whitelist gate lives entirely in actions.py (find_target / execute).
Claude gets exactly one tool, open_item, which calls straight into that
gate — there is no path from a model response to raw execution. If no
ANTHROPIC_API_KEY is set, Brain.enabled is False and main.py falls back
to the old pattern-matching "open X" handling, so Nova still works fully
offline.
"""

import os

from actions import execute, find_target

MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """You are {agent_name}, a small offline-first desktop voice assistant running on the user's Windows laptop.

You can hold a normal conversation and answer questions. You have exactly one capability beyond talking: opening an app or folder the user has pre-approved, via the open_item tool.

Rules:
- Only call open_item when the user is clearly asking you to open, launch, or go to something.
- Never claim you opened something unless the tool call reports success.
- If open_item reports the item isn't recognized, tell the user plainly and suggest they add it to config.json — do not guess at a different item or call the tool again with a different name.
- Keep replies short (1-2 sentences) — they are read aloud by text-to-speech."""

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


class Brain:
    def __init__(self, config: dict):
        self.config = config
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.enabled = bool(api_key) and config.get("use_ai_brain", True)

        if not self.enabled:
            self.client = None
            return

        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.system = SYSTEM_PROMPT.format(agent_name=config.get("agent_name", "Nova"))

    def respond(self, user_text: str) -> str:
        """Send user_text to Claude, running open_item if requested, and
        return the final reply to speak. Raises on API/network failure —
        callers should catch and fall back to the offline command path."""
        import anthropic

        messages = [{"role": "user", "content": user_text}]
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=self.system,
            tools=[OPEN_ITEM_TOOL],
            messages=messages,
        )

        # Bounded loop: open_item is the only tool, so this resolves in one
        # or two round trips in practice.
        for _ in range(5):
            if response.stop_reason != "tool_use":
                break

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": self._run_open_item(block.input.get("name", "")),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

            response = self.client.messages.create(
                model=MODEL,
                max_tokens=300,
                system=self.system,
                tools=[OPEN_ITEM_TOOL],
                messages=messages,
            )

        return "".join(block.text for block in response.content if block.type == "text").strip()

    def _run_open_item(self, target_phrase: str) -> str:
        kind, name, path = find_target(target_phrase, self.config)
        if not name:
            return f"'{target_phrase}' is not in config.json — cannot open it."
        ok = execute(kind, path)
        if ok:
            return f"Opened {name}."
        return f"Found {name} in config.json but the open call failed. Check its path."
