"""
discovery.py — read-only enumeration of apps already installed on the
laptop (Start Menu shortcuts + the registry's "App Paths" key), so
building config.json's app whitelist doesn't mean hand-typing every .exe
path. This module never executes anything and never writes to config.json
— it only reads Windows metadata and returns candidates. See
discover_apps.py for the interactive script that turns this into
config.json entries by hand.

brain.py does import find_installed_app() from here, to turn a plain
"not whitelisted" refusal into a helpful one ("I found it at <path> — run
discover_apps.py to add it"). That's still read-only lookup, not a write —
Brain never calls discover_installed_apps()/find_installed_app() results
back into config.json itself. The "Nova can't add to its own whitelist"
boundary only means GPT has no tool that writes to config.json; it doesn't
mean GPT can't know discovery.py exists.
"""

import json
import os
import subprocess
from difflib import get_close_matches
import tempfile
import winreg

STARTMENU_TIMEOUT_SECONDS = 20.0

APP_PATHS_REGISTRY_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def _app_paths_from_registry() -> dict:
    """name (without .exe) -> resolved path, from HKLM/HKCU App Paths.
    Never raises — any registry access failure just yields fewer results."""
    found = {}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, APP_PATHS_REGISTRY_KEY)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(key, subkey_name) as subkey:
                        path, _ = winreg.QueryValueEx(subkey, "")
                except OSError:
                    continue
                path = os.path.expandvars(path.strip('"'))
                if path and os.path.isfile(path):
                    name = os.path.splitext(subkey_name)[0]
                    found[name] = path
    return found


def _start_menu_shortcuts() -> dict:
    """display name -> resolved .exe path, from both Start Menu folders.
    Resolves every .lnk in one batched PowerShell call (Shell COM object)
    instead of one subprocess per shortcut. Never raises."""
    roots = [
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
    ]
    lnk_paths = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".lnk"):
                    lnk_paths.append(os.path.join(dirpath, f))

    if not lnk_paths:
        return {}

    list_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("\n".join(lnk_paths))
            list_path = f.name

        # Get-Content decorates each returned line with extra path metadata
        # (PSPath/PSParentPath/etc.), which ConvertTo-Json would otherwise
        # serialize as a nested object instead of a plain string — cast to
        # [string] to strip that decoration before it round-trips as JSON.
        script = (
            "$shell = New-Object -ComObject WScript.Shell; "
            f"$paths = Get-Content -LiteralPath '{list_path}'; "
            "$results = foreach ($p in $paths) { "
            "  $p = [string]$p; "
            "  try { $sc = $shell.CreateShortcut($p); "
            "  [PSCustomObject]@{ Link = $p; Target = $sc.TargetPath } } catch {} "
            "}; "
            "$results | ConvertTo-Json -Compress"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=STARTMENU_TIMEOUT_SECONDS,
        )
        data = json.loads(proc.stdout or "[]")
    except Exception:
        return {}
    finally:
        if list_path:
            try:
                os.remove(list_path)
            except OSError:
                pass

    if isinstance(data, dict):  # PowerShell unwraps a single-item array
        data = [data]

    found = {}
    for entry in data or []:
        link = entry.get("Link")
        target = entry.get("Target")
        if not link or not target or not target.lower().endswith(".exe"):
            continue
        if not os.path.isfile(target):
            continue
        name = os.path.splitext(os.path.basename(link))[0]
        found[name] = target
    return found


def discover_installed_apps(config: dict) -> list:
    """Return a sorted list of {"name", "path"} candidates not already in
    config['apps'] (matched by case-insensitive resolved path). Never
    raises — any discovery failure just yields fewer/no candidates."""
    try:
        candidates = {}
        candidates.update(_app_paths_from_registry())
        # Start Menu names are usually friendlier, so let them win on clash.
        candidates.update(_start_menu_shortcuts())
    except Exception:
        return []

    existing_paths = set()
    for raw_path in config.get("apps", {}).values():
        expanded = os.path.realpath(os.path.expandvars(os.path.expanduser(raw_path)))
        existing_paths.add(expanded.lower())

    results = []
    seen_paths = set()
    for name, path in candidates.items():
        real = os.path.realpath(path).lower()
        if real in existing_paths or real in seen_paths:
            continue
        seen_paths.add(real)
        results.append({"name": name, "path": path})

    results.sort(key=lambda e: e["name"].lower())
    return results


def find_installed_app(config: dict, query: str):
    """Fuzzy-match query against apps that are installed but NOT yet
    whitelisted (same candidates discover_installed_apps() would list).
    Returns {"name", "path"} for the best match, or None. Read-only, same
    as discover_installed_apps() — never raises."""
    candidates = discover_installed_apps(config)
    if not candidates:
        return None

    needle = query.strip().lower()
    if not needle:
        return None

    # substring match first, same style as actions.py::find_target
    for candidate in candidates:
        name = candidate["name"].lower()
        if name in needle or needle in name:
            return candidate

    names = [c["name"] for c in candidates]
    close = get_close_matches(query, names, n=1, cutoff=0.6)
    if close:
        for candidate in candidates:
            if candidate["name"] == close[0]:
                return candidate
    return None
