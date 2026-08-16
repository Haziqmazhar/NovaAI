"""
transcript_window.py — Nova's dashboard, rendered as local HTML/CSS/JS
(dashboard.html) inside a plain native window via pywebview — no browser
chrome, no address bar, just a normal desktop window. This is a
view-layer swap: brain.py and main.py only ever call the public methods
below (log_line, set_state, handle_agent_event, get_command,
request_quit, run), so nothing else in the app changed.

pywebview's evaluate_js()/destroy() are safe to call from a background
thread while webview.start() blocks the thread that created the window
(confirmed empirically before building this) — so, unlike the earlier
tkinter version, outbound UI updates call straight into evaluate_js with
no queue/polling needed. The one thing that *does* still need a queue is
the JS -> Python direction: pywebview delivers js_api calls on its own
internal thread, and main.py's command loop polls get_command() from a
different thread, so that handoff still goes through a Queue.

dashboard.html owns all the visual/animation logic (the line-art robot
characters, task history table, console) — it's fully self-contained,
no other file in the project defines any of the character/animation
design.
"""

import json
import queue
from pathlib import Path

import webview

_DASHBOARD_HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


class TranscriptWindow:
    def __init__(self, agent_name: str, on_quit=None, text_input: bool = False,
                 agents=None, brain_mode: str = ""):
        self._input_queue = queue.Queue()
        self._on_quit = on_quit
        self._agents = agents or [("brain", "Brain", "#b46bff")]
        self._agent_name = agent_name
        self._brain_mode = brain_mode
        self._text_input = text_input

        self._window = webview.create_window(
            f"{agent_name} — dashboard",
            html=_DASHBOARD_HTML,
            js_api=_Api(self._input_queue),
            width=1200,
            height=720,
            min_size=(940, 600),
            background_color="#14161c",
        )
        self._window.events.closed += self._handle_close

    def _handle_close(self):
        if self._on_quit:
            self._on_quit()

    def _safe_eval(self, js: str):
        # The window may not be ready yet (very first "online" message can
        # race window startup) or may already be closing — either is a
        # normal, non-fatal race, not a bug worth surfacing.
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass

    def set_state(self, state: str, detail: str = ""):
        self._safe_eval(f"setStatus({json.dumps(state)}, {json.dumps(detail)})")

    def log_line(self, text: str):
        self._safe_eval(f"appendLog({json.dumps(text)})")

    def handle_agent_event(self, event: dict):
        """Called from Brain (background thread) on every agent state
        transition. Drives the workspace panel's sprites, status rings,
        traveling packets, and the task-history table."""
        self._safe_eval(f"applyAgentEvent({json.dumps(event)})")

    def get_command(self, timeout: float = 0.5):
        """Blocks up to `timeout` seconds for a typed command; returns None
        on timeout so callers can periodically check a quit flag."""
        try:
            return self._input_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def request_quit(self):
        self._window.destroy()

    def run(self):
        def on_ready():
            self._safe_eval(f"initAgents({json.dumps(self._agents)})")
            self._safe_eval(f"setHeader({json.dumps(self._agent_name)}, {json.dumps(self._brain_mode)})")
            if not self._text_input:
                self._safe_eval("setInputVisible(false)")

        webview.start(on_ready)


class _Api:
    """Exposed to dashboard.html as `pywebview.api`."""

    def __init__(self, input_queue: queue.Queue):
        self._input_queue = input_queue

    def submit_command(self, text: str):
        text = (text or "").strip()
        if text:
            self._input_queue.put(text)
