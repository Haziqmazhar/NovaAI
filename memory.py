"""
memory.py — Nova's durable, cross-session memory: a single append-only
markdown file the user points config.json's "memory_path" at (their own
folder, or a subfolder of an Obsidian vault — any plain markdown file
works). Off by default ("memory_enabled": false in config.json) — this and
download_file (subagents.py) are the only two places in the codebase that
write to disk; everything else is read-only or launches pre-approved
apps/folders.

Deliberately narrow, the same way download_file is deliberately narrow in
its own way: Nova always writes to this one exact path from config.json,
never a filename supplied by the model, so there's no traversal surface to
guard against the way read_document needs one. Writes are append-only —
remember() can add a line, never overwrite or edit an existing one — so a
bad tool call can add a stray note but can never destroy previously saved
memory. If something in there is wrong, the user can just open the file
(in Obsidian or any editor) and fix it directly — Nova has no forget/edit
tool of its own.
"""
import os
from datetime import datetime

MAX_MEMORY_CHARS = 4000  # how much of the file gets folded into the system prompt each turn
DEFAULT_MEMORY_PATH = "memory.md"
HEADING = "# Nova's memory\n\n"
NOTHING_REMEMBERED = "(nothing remembered yet)"


def _resolve_path(config: dict) -> str:
    raw = config.get("memory_path") or DEFAULT_MEMORY_PATH
    path = os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(__file__), path)
    return path


def append_memory(config: dict, note: str) -> None:
    """Append one timestamped line to the memory file, creating it (with
    HEADING) on first use. Never truncates or rewrites existing content."""
    path = _resolve_path(config)
    is_new = not os.path.exists(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        if is_new:
            f.write(HEADING)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        f.write(f"- {timestamp} — {note}\n")


def read_memory(config: dict) -> str:
    """Return the memory file's content, capped to MAX_MEMORY_CHARS keeping
    the most RECENT entries (recent context matters more than the oldest
    thing Nova ever learned). Never raises."""
    path = _resolve_path(config)
    if not os.path.exists(path):
        return NOTHING_REMEMBERED
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    if len(text) > MAX_MEMORY_CHARS:
        text = "[...older entries truncated...]\n" + text[-MAX_MEMORY_CHARS:]
    return text.strip() or NOTHING_REMEMBERED
