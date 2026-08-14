"""
transcript_window.py — a small always-on-top window showing what Nova
hears/reads and says, plus a live status line. This is the "visible status
window" from the project plan's Phase 1 — a step up from the tray-only
UI, without pulling in a heavier GUI framework than the stdlib's tkinter.

When voice recognition is disabled (config.json "voice_enabled": false),
the window also shows a text entry box so you can type commands directly
instead of speaking them — useful for developing the agent/display without
a microphone in the loop.

Nova's command loop runs on a background thread; tkinter must only be
touched from the thread that created it. All cross-thread communication
goes through thread-safe queues that the Tk mainloop polls / that the
background thread polls with a timeout.
"""

import queue
import tkinter as tk
from tkinter import scrolledtext

STATE_COLORS = {
    "idle": "#888888",
    "listening": "#1e90ff",
    "executing": "#32cd32",
    "error": "#dc143c",
}


class TranscriptWindow:
    def __init__(self, agent_name: str, on_quit=None, text_input: bool = False):
        self._queue = queue.Queue()
        self._input_queue = queue.Queue()
        self._on_quit = on_quit

        self.root = tk.Tk()
        self.root.title(f"{agent_name} — transcript")
        self.root.geometry("420x420" if text_input else "420x360")
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)

        self.status_label = tk.Label(
            self.root, text="idle", fg=STATE_COLORS["idle"], font=("Segoe UI", 11, "bold")
        )
        self.status_label.pack(pady=(8, 4))

        self.log = scrolledtext.ScrolledText(
            self.root, wrap=tk.WORD, state="disabled", font=("Segoe UI", 10)
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        if text_input:
            entry_frame = tk.Frame(self.root)
            entry_frame.pack(fill="x", padx=8, pady=(0, 8))

            self.entry = tk.Entry(entry_frame, font=("Segoe UI", 10))
            self.entry.pack(side="left", fill="x", expand=True)
            self.entry.bind("<Return>", self._submit)
            self.entry.focus_set()

            send_button = tk.Button(entry_frame, text="Send", command=self._submit)
            send_button.pack(side="left", padx=(6, 0))

        self.root.after(100, self._poll)

    def _submit(self, event=None):
        text = self.entry.get().strip()
        if text:
            self._input_queue.put(text)
            self.entry.delete(0, tk.END)

    def _handle_close(self):
        if self._on_quit:
            self._on_quit()
        self.root.destroy()

    def _poll(self):
        try:
            while True:
                kind, text = self._queue.get_nowait()
                if kind == "state":
                    state_key = text.split(" ")[0]
                    self.status_label.config(text=text, fg=STATE_COLORS.get(state_key, "#888888"))
                elif kind == "log":
                    self.log.config(state="normal")
                    self.log.insert(tk.END, text + "\n")
                    self.log.see(tk.END)
                    self.log.config(state="disabled")
                elif kind == "quit":
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def set_state(self, state: str, detail: str = ""):
        label = f"{state} ({detail})" if detail else state
        self._queue.put(("state", label))

    def log_line(self, text: str):
        self._queue.put(("log", text))

    def get_command(self, timeout: float = 0.5):
        """Blocks up to `timeout` seconds for a typed command; returns None
        on timeout so callers can periodically check a quit flag."""
        try:
            return self._input_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def request_quit(self):
        """Thread-safe: ask the Tk mainloop to close the window."""
        self._queue.put(("quit", ""))

    def run(self):
        self.root.mainloop()
