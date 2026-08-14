"""
tray.py — a small system tray icon so you can SEE what Nova is doing
(idle / listening / executing) without a full GUI window. Right-click
the tray icon to quit.
"""

import threading

import pystray
from PIL import Image, ImageDraw


def _make_icon(color: str) -> Image.Image:
    img = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=color)
    return img


STATE_COLORS = {
    "idle": "gray",
    "listening": "deepskyblue",
    "executing": "limegreen",
    "error": "red",
}


class StatusTray:
    def __init__(self, on_quit=None):
        self._icon = pystray.Icon(
            "nova",
            _make_icon(STATE_COLORS["idle"]),
            "Nova — idle (say 'hey nova')",
            menu=pystray.Menu(
                pystray.MenuItem("Quit Nova", self._quit),
            ),
        )
        self._on_quit = on_quit

    def _quit(self, icon, item):
        icon.stop()
        if self._on_quit:
            self._on_quit()

    def set_state(self, state: str, detail: str = ""):
        color = STATE_COLORS.get(state, "gray")
        self._icon.icon = _make_icon(color)
        label = f"Nova — {state}"
        if detail:
            label += f" ({detail})"
        self._icon.title = label[:127]  # tray tooltip length limit

    def run_in_background(self):
        thread = threading.Thread(target=self._icon.run, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self._icon.stop()
