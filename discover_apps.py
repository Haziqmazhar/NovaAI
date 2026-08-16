"""
discover_apps.py — interactive utility to bulk-add installed apps into
config.json's whitelist. Run it yourself whenever you want to expand what
Nova can open: python discover_apps.py

This is a human-run setup tool, not a Nova/GPT capability — Brain never
imports discovery.py, so this can't be triggered by voice/chat and doesn't
widen what Nova can do on its own. It only writes to config.json after you
pick which apps to add and confirm, and takes a config.json.bak backup
first.

Nova only reads config.json once at startup, so restart it after adding
apps for the change to take effect.
"""

import json
import os
import shutil
import sys

from discovery import discover_installed_apps

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
BACKUP_PATH = CONFIG_PATH + ".bak"


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(f"ERROR: config.json not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_selection(raw: str, count: int) -> list:
    raw = raw.strip().lower()
    if not raw:
        return []
    if raw == "all":
        return list(range(count))
    indices = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        n = int(part) - 1
        if 0 <= n < count:
            indices.append(n)
    return indices


def unique_key(base_key: str, taken: set) -> str:
    if base_key not in taken:
        return base_key
    n = 2
    while f"{base_key}_{n}" in taken:
        n += 1
    return f"{base_key}_{n}"


def main():
    config = load_config()
    candidates = discover_installed_apps(config)

    if not candidates:
        print("No new apps found - everything discoverable is already in your "
              "whitelist (or nothing was found).")
        return

    print(f"Found {len(candidates)} app(s) not yet in config.json:\n")
    for i, entry in enumerate(candidates, start=1):
        print(f"  {i:2}. {entry['name']}  -  {entry['path']}")

    raw = input("\nEnter numbers to add (comma-separated), 'all', or blank to cancel: ")
    selected = parse_selection(raw, len(candidates))
    if not selected:
        print("Nothing added.")
        return

    taken_keys = set(config.get("apps", {}).keys())
    to_add = {}
    for i in selected:
        entry = candidates[i]
        key = unique_key(entry["name"].lower().replace(" ", "_"), taken_keys | set(to_add.keys()))
        to_add[key] = entry["path"]

    print(f"\nWill add {len(to_add)} app(s) to config.json:")
    for key, path in to_add.items():
        print(f"  {key}: {path}")

    confirm = input("\nWrite these to config.json? (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Cancelled - nothing written.")
        return

    shutil.copyfile(CONFIG_PATH, BACKUP_PATH)
    config.setdefault("apps", {}).update(to_add)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print(f"\nAdded {len(to_add)} app(s) to config.json (backup saved to {os.path.basename(BACKUP_PATH)}):")
    for key, path in to_add.items():
        print(f"  {key}: {path}")
    print("\nRestart Nova for the change to take effect.")


if __name__ == "__main__":
    main()
