"""
tts.py — offline text-to-speech, so Nova can talk back.
Uses pyttsx3, which wraps Windows' built-in SAPI5 voices (no internet, no API key).
"""

import pyttsx3


class Voice:
    def __init__(self, rate: int = 180):
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", rate)

    def say(self, text: str):
        print(f"[Nova] {text}")
        self.engine.say(text)
        self.engine.runAndWait()
