"""
actions.py — everything Nova is actually allowed to DO.

SECURITY MODEL (read this):
Nova will only ever open things listed by name in config.json, under "apps"
or "folders". There is no code path here that runs an arbitrary string as a
shell command from voice input. If you want Nova to be able to open
something new, you add it to config.json yourself — Nova can't add to its
own whitelist. This is intentional: it keeps a misheard word, a bug, or
someone else's voice near your laptop from making Nova do something you
didn't approve.
"""

import os
import subprocess
from difflib import get_close_matches


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def find_target(spoken_name: str, config: dict):
    """
    Match spoken text against the whitelisted apps/folders in config.
    Returns (kind, key, path) or (None, None, None) if nothing matched
    closely enough.
    """
    spoken_name = spoken_name.strip().lower()
    apps = config.get("apps", {})
    folders = config.get("folders", {})
    all_names = list(apps.keys()) + list(folders.keys())

    # exact substring match first (e.g. "open google chrome" -> "chrome")
    for name in all_names:
        if name in spoken_name:
            if name in apps:
                return "app", name, _expand(apps[name])
            return "folder", name, _expand(folders[name])

    # fuzzy fallback for near-misses from imperfect speech recognition
    close = get_close_matches(spoken_name, all_names, n=1, cutoff=0.6)
    if close:
        name = close[0]
        if name in apps:
            return "app", name, _expand(apps[name])
        return "folder", name, _expand(folders[name])

    return None, None, None


def open_app(path: str) -> bool:
    try:
        subprocess.Popen([path], shell=False)
        return True
    except FileNotFoundError:
        try:
            # fall back to shell resolution for things like "notepad.exe"
            # that rely on PATH rather than a full path
            subprocess.Popen(path, shell=True)
            return True
        except Exception:
            return False
    except Exception:
        return False


def open_folder(path: str) -> bool:
    try:
        os.startfile(path)  # Windows-only, opens in File Explorer
        return True
    except Exception:
        return False


def execute(kind: str, path: str) -> bool:
    if kind == "app":
        return open_app(path)
    if kind == "folder":
        return open_folder(path)
    return False
