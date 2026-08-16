"""
Standalone test of discover_installed_apps()'s merge/dedup logic in
discovery.py. Like the other test_*_logic.py files, this has zero
network/API-key dependency, and doesn't depend on the real machine's
registry/Start Menu contents — it monkeypatches the two private discovery
functions with canned data instead.
"""
import discovery

_original_registry = discovery._app_paths_from_registry
_original_start_menu = discovery._start_menu_shortcuts

try:
    discovery._app_paths_from_registry = lambda: {
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "notepad++": r"C:\Program Files\Notepad++\notepad++.exe",
    }
    discovery._start_menu_shortcuts = lambda: {
        "Google Chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",  # same path, different name
        "Notepad++": r"C:\Program Files\Notepad++\notepad++.exe",  # same path, different case in name
        "Spotify": r"C:\Users\Amir\AppData\Roaming\Spotify\Spotify.exe",
    }

    config = {
        "apps": {
            # already whitelisted under a different key/casing -> must be excluded
            "notepad_plus_plus": r"c:\program files\notepad++\NOTEPAD++.EXE",
        }
    }

    print("=== discover_installed_apps ===")
    results = discovery.discover_installed_apps(config)
    names = [r["name"] for r in results]
    print(results)

    # notepad++ is already whitelisted (case/path variant) -> excluded entirely
    assert "notepad++" not in names and "Notepad++" not in names
    print("excludes already-whitelisted path (case-insensitive): OK")

    # chrome appears in both sources with the same real path -> exactly one entry
    chrome_entries = [r for r in results if r["path"].lower().endswith("chrome.exe")]
    assert len(chrome_entries) == 1, chrome_entries
    print("dedupes same path across both sources: OK")

    # spotify only came from Start Menu -> still included
    assert any(r["name"] == "Spotify" for r in results)
    print("includes Start-Menu-only candidate: OK")

    # sorted by name, case-insensitive
    assert names == sorted(names, key=str.lower)
    print("sorted by name: OK")

    print("\n=== empty/failure cases ===")
    discovery._app_paths_from_registry = lambda: {}
    discovery._start_menu_shortcuts = lambda: {}
    assert discovery.discover_installed_apps({"apps": {}}) == []
    print("no candidates: OK")

    def _raises():
        raise OSError("boom")

    discovery._app_paths_from_registry = _raises
    assert discovery.discover_installed_apps({"apps": {}}) == []
    print("discovery failure degrades to empty list: OK")

    print("\nAll sanity assertions passed.")
finally:
    discovery._app_paths_from_registry = _original_registry
    discovery._start_menu_shortcuts = _original_start_menu
