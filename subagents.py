"""
subagents.py — specialized sub-agents the orchestrator (brain.py) can
delegate to. Each sub-agent gets its own narrow, tool-less Claude call and
zero direct system access — the only thing it can touch is what its own
function explicitly reads, inside an explicit whitelist check. Extend
this file with one function per new sub-agent capability.
"""

import os

MAX_CHARS = 20_000
REQUEST_TIMEOUT_SECONDS = 30.0

DOCUMENT_AGENT_SYSTEM_PROMPT = (
    "You are a document analysis sub-agent. You are given the text "
    "contents of exactly one file and an instruction. Do only what the "
    "instruction asks, using only the given content — you have no tools "
    "and no other context. Be concise."
)


def _resolve_whitelisted_file(config: dict, file_name: str):
    """Find file_name inside one of config['folders'], refusing to leave
    the whitelisted folder even via '..' or symlink tricks. Returns the
    resolved absolute path, or None if not found / not allowed."""
    folders = config.get("folders", {})
    target_basename = os.path.basename(file_name)

    for folder_path in folders.values():
        root = os.path.realpath(os.path.expandvars(os.path.expanduser(folder_path)))
        if not os.path.isdir(root):
            continue
        candidate = os.path.realpath(os.path.join(root, target_basename))
        if os.path.commonpath([root, candidate]) == root and os.path.isfile(candidate):
            return candidate
    return None


def _extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()

    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS] + "\n[...truncated...]"
    return text


def run_document_agent(config: dict, client, model: str, instruction: str, file_name: str) -> str:
    """Read a whitelisted file and have a scoped Claude call act on it per
    `instruction`. Returns the result text, or a plain-language error
    string on any failure — never raises, so a bad file/instruction can't
    crash the orchestrator's turn."""
    path = _resolve_whitelisted_file(config, file_name)
    if not path:
        return f"'{file_name}' was not found in any whitelisted folder — cannot read it."

    try:
        text = _extract_text(path)
    except Exception as e:
        return f"Could not read '{file_name}': {e}"

    if not text.strip():
        return f"'{file_name}' appears to be empty, or its text couldn't be extracted."

    prompt = f"Instruction: {instruction}\n\nFile contents:\n{text}"

    last_error = None
    for _attempt in range(2):  # one retry on a transient API failure
        try:
            response = client.with_options(timeout=REQUEST_TIMEOUT_SECONDS).messages.create(
                model=model,
                max_tokens=1000,
                system=DOCUMENT_AGENT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in response.content if block.type == "text").strip()
        except Exception as e:
            last_error = e

    return f"The document agent failed to process '{file_name}': {last_error}"
