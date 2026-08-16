"""
Standalone test of memory.py's file I/O logic. Like test_actions_logic.py,
this has zero network/API-key dependency — it uses a fixture path in a
temp directory, never real config.json paths, so it can't touch anything
outside a throwaway temp dir.
"""
import os
import shutil
import tempfile

from memory import HEADING, MAX_MEMORY_CHARS, NOTHING_REMEMBERED, append_memory, read_memory

tmp_root = tempfile.mkdtemp(prefix="nova_memory_test_")

try:
    print("=== read_memory before any writes ===")
    missing_path = os.path.join(tmp_root, "does_not_exist.md")
    assert read_memory({"memory_path": missing_path}) == NOTHING_REMEMBERED
    print("placeholder when missing: OK")

    print("\n=== append_memory / read_memory round trip ===")
    path = os.path.join(tmp_root, "nested", "memory.md")  # not-yet-created subfolder
    config = {"memory_path": path}

    append_memory(config, "Prefers dark roast coffee")
    text = read_memory(config)
    assert text.startswith(HEADING.strip()), text
    assert "Prefers dark roast coffee" in text
    print("creates file + heading on first write: OK")

    append_memory(config, "Dog's name is Max")
    text = read_memory(config)
    assert text.count(HEADING.strip()) == 1  # heading never duplicated
    assert "Prefers dark roast coffee" in text and "Dog's name is Max" in text
    print("second append preserves first entry, no duplicate heading (append-only): OK")

    print("\n=== truncation keeps the most recent entries ===")
    big_path = os.path.join(tmp_root, "big.md")
    big_config = {"memory_path": big_path}
    for i in range(2000):
        append_memory(big_config, f"note number {i}")
    text = read_memory(big_config)
    assert len(text) <= MAX_MEMORY_CHARS + 200  # + truncation marker overhead
    assert "note number 1999" in text  # most recent entry survives
    assert "note number 0" not in text  # oldest entry got truncated away
    assert "truncated" in text
    print("keeps recent entries, drops oldest, stays within budget: OK")

    print("\nAll sanity assertions passed.")
finally:
    shutil.rmtree(tmp_root, ignore_errors=True)
