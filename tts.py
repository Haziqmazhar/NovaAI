"""
tts.py — offline text-to-speech, so Nova can talk back.
Uses pyttsx3, which wraps Windows' built-in SAPI5 voices (no internet, no API key).

Can be disabled entirely (config.json "tts_enabled": false) — Nova still
prints and logs every reply to the transcript window, it just stays quiet.
"""

import pyttsx3


class Voice:
    def __init__(self, rate: int = 180, enabled: bool = True):
        self.enabled = enabled
        self.engine = None
        if self.enabled:
            self.engine = pyttsx3.init()
            self.engine.setProperty("rate", rate)

    def say(self, text: str):
        print(f"[Nova] {text}")
        if not self.enabled:
            return
        self.engine.say(text)
        self.engine.runAndWait()
