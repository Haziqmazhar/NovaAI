"""
Standalone test of the reminder validation/formatting logic in brain.py.
Like test_actions_logic.py, this has zero network/API-key dependency —
_validate_delay and _humanize_seconds are pure functions, so this never
starts a real threading.Timer or touches OpenAI.
"""
from brain import MAX_REMINDER_DELAY_SECONDS, _humanize_seconds, _validate_delay

print("=== _validate_delay ===")
assert _validate_delay(45) == 45
assert _validate_delay("45") == 45  # tool args arrive as JSON, ints survive round-trip fine
print("valid int: OK")

assert _validate_delay(MAX_REMINDER_DELAY_SECONDS + 1000) == MAX_REMINDER_DELAY_SECONDS
print("clamped above max: OK")

for bad in (0, -5, None, "soon", "not-a-number"):
    try:
        _validate_delay(bad)
        raise AssertionError(f"expected ValueError for {bad!r}")
    except ValueError:
        pass
print("rejects <1 and non-numeric: OK")

print("\n=== _humanize_seconds ===")
assert _humanize_seconds(45) == "45s"
assert _humanize_seconds(90) == "1m 30s"
assert _humanize_seconds(120) == "2m"
assert _humanize_seconds(3661) == "1h 1m"
assert _humanize_seconds(3600) == "1h"
assert _humanize_seconds(90000) == "1d 1h"
print("boundary values: OK")

print("\nAll sanity assertions passed.")
