"""
coding_agent.py — lets Nova launch a real coding agent (the Claude Code
CLI) in a new, visible console window, scoped to one pre-approved
repository directory from config.json["repos"]. Nova itself never reads
or writes a single file in the repo — it only starts the process, the
same "launch a pre-approved thing" pattern actions.py::execute() already
uses for apps. Whatever the coding agent actually does happens inside its
own separate, already permission-gated session, completely outside
Nova's own process and write boundary.

Deliberately no task-seeding in this version: passing dynamic text into a
.cmd-based CLI launch via `cmd /c` risks cmd.exe's own metacharacter
handling (%, &, |, ^), which is fiddly to make airtight. repo_name is
only ever used as a dict-key lookup into config["repos"] here — never
concatenated into a command string — so there is no injection surface.
"""

import os
import shutil
import subprocess

CLAUDE_CLI_NAME = "claude"


def _resolve_whitelisted_repo(config: dict, repo_name: str):
    """Match repo_name against config['repos'] (case-insensitive
    exact/substring, same style as actions.py::find_target). Returns
    (key, root_path) or (None, None) if nothing matched."""
    repos = config.get("repos", {})
    if not repo_name:
        return None, None

    needle = repo_name.strip().lower()
    for key, path in repos.items():
        if needle in key.lower() or key.lower() in needle:
            root = os.path.realpath(os.path.expandvars(os.path.expanduser(path)))
            if os.path.isdir(root):
                return key, root
    return None, None


def launch_coding_agent(config: dict, repo_name: str) -> str:
    """Open a new console window running the Claude Code CLI in one
    whitelisted repo directory. Never raises — returns a plain-language
    result either way."""
    key, root = _resolve_whitelisted_repo(config, repo_name)
    if not root:
        known = ", ".join(sorted(config.get("repos", {}).keys())) or "none configured"
        return f"'{repo_name}' isn't one of the whitelisted repos. Repos available: {known}."

    claude_path = shutil.which(CLAUDE_CLI_NAME)
    if not claude_path:
        return (
            "Claude Code's CLI ('claude') isn't on PATH, so I can't launch it. "
            "Install it first, then try again."
        )

    comspec = os.environ.get("ComSpec", "cmd.exe")
    try:
        subprocess.Popen(
            [comspec, "/c", claude_path],
            cwd=root,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except Exception as e:
        return f"Failed to launch Claude Code in '{key}': {e}"

    return f"Opened Claude Code in a new window, working in the '{key}' repo."
