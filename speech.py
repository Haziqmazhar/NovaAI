"""
speech.py — offline speech-to-text using Vosk.

Vosk listens to the microphone in small chunks and turns audio into text
entirely on-device. No audio ever leaves your computer.
"""

import json
import queue
import sys
import time

import sounddevice as sd
from vosk import KaldiRecognizer, Model


class SpeechListener:
    def __init__(self, model_path: str, sample_rate: int = 16000):
        try:
            self.model = Model(model_path)
        except Exception as e:
            print(
                f"\n[Nova] ERROR: could not load Vosk model from '{model_path}'.\n"
                f"Did you download a model and point vosk_model_path at it in config.json?\n"
                f"See README.md for the download link.\nDetails: {e}\n"
            )
            sys.exit(1)

        self.sample_rate = sample_rate

    def listen_once(self, timeout_seconds: float = 5.0):
        """
        Opens the mic, waits for a single finished utterance (or timeout),
        and returns it as text (lowercase). Returns "" on timeout/silence.
        """
        recognizer = KaldiRecognizer(self.model, self.sample_rate)
        result_text = ""
        start = time.time()

        q = queue.Queue()

        def cb(indata, frames, time_info, status):
            q.put(bytes(indata))

        with sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=8000,
            dtype="int16",
            channels=1,
            callback=cb,
        ):
            while time.time() - start < timeout_seconds:
                try:
                    data = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    result_text = result.get("text", "").strip()
                    if result_text:
                        break

        return result_text.lower()
