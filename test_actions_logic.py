"""
Standalone test of the whitelist-matching logic in actions.py.
This part has zero Windows/audio dependency, so it's the one piece
I can actually execute and verify here in the sandbox.
"""
import json
from actions import find_target

config = json.load(open("config.json"))

test_cases = [
    "google chrome",      # should fuzzy/substring match "chrome"
    "chrome",
    "my downloads folder", # should match "downloads"
    "notepad please",
    "spotify",
    "some totally unknown app xyz",  # should NOT match anything
]

print(f"{'spoken phrase':35} -> {'kind':8} {'matched key':12} path")
print("-" * 90)
all_ok = True
for phrase in test_cases:
    kind, name, path = find_target(phrase, config)
    print(f"{phrase:35} -> {str(kind):8} {str(name):12} {path}")

# Sanity assertions
assert find_target("chrome", config)[1] == "chrome"
assert find_target("my downloads folder", config)[1] == "downloads"
assert find_target("some totally unknown app xyz", config)[0] is None
print("\nAll sanity assertions passed.")
