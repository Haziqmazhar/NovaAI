"""
Standalone test of coding_agent.py's whitelist/launch logic. Like the
other test_*_logic.py files, this has zero network dependency and never
spawns a real process or window — subprocess.Popen and shutil.which are
monkeypatched.
"""
import os
import shutil
import subprocess
import tempfile

import coding_agent

_original_which = shutil.which
_original_popen = subprocess.Popen

tmp_root = tempfile.mkdtemp(prefix="nova_test_repo_")
config = {"repos": {"nova": tmp_root}}

popen_calls = []


def fake_popen(args, **kwargs):
    popen_calls.append((args, kwargs))

    class _FakeProc:
        pid = 12345

    return _FakeProc()


try:
    print("=== _resolve_whitelisted_repo ===")
    key, root = coding_agent._resolve_whitelisted_repo(config, "nova")
    assert key == "nova" and root == os.path.realpath(tmp_root), (key, root)
    print("exact match: OK")

    key, root = coding_agent._resolve_whitelisted_repo(config, "the nova repo")
    assert key == "nova"
    print("substring match: OK")

    key, root = coding_agent._resolve_whitelisted_repo(config, "nonexistent")
    assert key is None and root is None
    print("unknown repo: OK")

    print("\n=== launch_coding_agent: claude not on PATH ===")
    shutil.which = lambda name: None
    subprocess.Popen = fake_popen
    result = coding_agent.launch_coding_agent(config, "nova")
    print(result)
    assert "isn't on PATH" in result, result
    assert popen_calls == [], "Popen must never be called when claude is missing"
    print("missing claude CLI: OK")

    print("\n=== launch_coding_agent: unknown repo ===")
    shutil.which = lambda name: r"C:\fake\claude.cmd"
    result = coding_agent.launch_coding_agent(config, "not_a_repo")
    print(result)
    assert "isn't one of the whitelisted repos" in result, result
    assert popen_calls == []
    print("unknown repo blocked before Popen: OK")

    print("\n=== launch_coding_agent: success path ===")
    result = coding_agent.launch_coding_agent(config, "nova")
    print(result)
    assert "Opened Claude Code" in result and "'nova' repo" in result, result
    assert len(popen_calls) == 1
    args, kwargs = popen_calls[0]
    assert args[-1] == r"C:\fake\claude.cmd", args
    assert args[0].lower().endswith("cmd.exe") or "cmd" in args[0].lower(), args
    assert args[1] == "/c"
    assert kwargs.get("cwd") == os.path.realpath(tmp_root)
    assert kwargs.get("creationflags") == subprocess.CREATE_NEW_CONSOLE
    assert "shell" not in kwargs, "must never use shell=True"
    print("Popen called with expected argv/cwd/creationflags, no shell=True: OK")

    print("\nAll sanity assertions passed.")
finally:
    shutil.which = _original_which
    subprocess.Popen = _original_popen
    shutil.rmtree(tmp_root, ignore_errors=True)
