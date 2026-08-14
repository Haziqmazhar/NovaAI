"""
main.py — Nova, a small voice-activated desktop agent.

How it works, in plain terms:
  1. If "voice_enabled" is true in config.json, Nova listens on the
     microphone for the wake word ("hey nova" by default), says "Yes?",
     then listens once more for your actual command. If it's false (the
     current default, while the agent/display are still being built out),
     Nova skips Vosk and the microphone entirely and instead takes typed
     commands from the transcript window — no wake word needed there,
     since typing is already an explicit action.
  2. If an ANTHROPIC_API_KEY is set, the command goes to Claude
     (brain.py), which can chat normally and has exactly one tool —
     open_item — wired straight into the same whitelist gate as before.
     Without a key, Nova falls back to the original "open X" pattern
     match, so it still works fully offline.
  3. A transcript window shows what Nova heard/read and said; the tray
     icon keeps showing idle/listening/executing at a glance.

Run it with:  python main.py
Quit it by:   close the transcript window, right-click the tray icon ->
              Quit Nova, or Ctrl+C in the terminal.
"""

import json
import os
import sys
import threading

from actions import execute, find_target
from brain import Brain
from tray import StatusTray
from transcript_window import TranscriptWindow
from tts import Voice

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"[Nova] ERROR: config.json not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def strip_wake_word(text: str, wake_word: str) -> str:
    """If the command was said in the same breath as the wake word
    ('hey nova open chrome'), strip the wake word off the front."""
    if text.startswith(wake_word):
        return text[len(wake_word):].strip()
    return text


def voice_loop(config: dict, listener, voice: Voice, brain: Brain,
                tray: StatusTray, window: TranscriptWindow, should_quit: threading.Event):
    wake_word = config.get("wake_word", "hey nova").lower()
    listen_after_wake = config.get("listen_timeout_after_wake_seconds", 6)

    print(f"[Nova] Ready. Say '{wake_word}' to activate me. (Ctrl+C or tray menu to quit)")
    mode = "Claude-powered" if brain.enabled else "offline pattern-matching"
    print(f"[Nova] Command handling: {mode}")
    window.log_line(f"[Nova] Command handling: {mode}")
    voice.say(f"{config.get('agent_name', 'Nova')} is online.")

    try:
        while not should_quit.is_set():
            tray.set_state("listening", "for wake word")
            window.set_state("listening", "for wake word")
            heard = listener.listen_once(timeout_seconds=4.0)

            if not heard or wake_word not in heard:
                continue

            remainder = strip_wake_word(heard, wake_word)

            if remainder:
                command_text = remainder
            else:
                tray.set_state("listening", "for command")
                window.set_state("listening", "for command")
                voice.say("Yes?")
                command_text = listener.listen_once(timeout_seconds=listen_after_wake)

            if not command_text:
                voice.say("I didn't catch a command.")
                continue

            handle_command(command_text, config, voice, brain, tray, window)

    except KeyboardInterrupt:
        pass
    finally:
        voice.say("Going offline.")
        print("[Nova] Shutting down.")
        window.request_quit()
        tray.stop()


def text_loop(config: dict, voice: Voice, brain: Brain,
              tray: StatusTray, window: TranscriptWindow, should_quit: threading.Event):
    print("[Nova] Voice recognition is disabled (voice_enabled: false in config.json).")
    print("[Nova] Type a command in the transcript window and press Enter. (Ctrl+C or tray menu to quit)")
    mode = "Claude-powered" if brain.enabled else "offline pattern-matching"
    print(f"[Nova] Command handling: {mode}")
    window.log_line(f"[Nova] Command handling: {mode}")
    window.log_line("Voice recognition is off — type a command below and press Enter.")
    voice.say(f"{config.get('agent_name', 'Nova')} is online.")

    tray.set_state("idle")
    window.set_state("idle")

    try:
        while not should_quit.is_set():
            command_text = window.get_command(timeout=0.5)
            if not command_text:
                continue
            handle_command(command_text, config, voice, brain, tray, window)
    except KeyboardInterrupt:
        pass
    finally:
        voice.say("Going offline.")
        print("[Nova] Shutting down.")
        window.request_quit()
        tray.stop()


def handle_command(command_text: str, config: dict, voice: Voice, brain: Brain,
                    tray: StatusTray, window: TranscriptWindow):
    print(f"[Nova] Heard command: '{command_text}'")
    window.log_line(f"You: {command_text}")

    if brain.enabled:
        tray.set_state("executing", "thinking")
        window.set_state("executing", "thinking")
        try:
            reply = brain.respond(command_text)
        except Exception as e:
            print(f"[Nova] Claude API call failed, falling back to offline handling: {e}")
            window.log_line(f"[Nova] Claude API call failed ({e}); handling offline instead.")
            reply = None

        if reply:
            voice.say(reply)
            window.log_line(f"Nova: {reply}")
            tray.set_state("idle")
            window.set_state("idle")
            return
        # Fall through to offline handling on API failure or empty reply.

    handle_command_offline(command_text, config, voice, tray, window)


def handle_command_offline(command_text: str, config: dict, voice: Voice,
                            tray: StatusTray, window: TranscriptWindow):
    # Offline fallback: v1's simple "open <thing>" pattern match — no AI,
    # no network, same whitelist gate as always.
    if "open" in command_text:
        target_phrase = command_text.split("open", 1)[1].strip()
        kind, name, path = find_target(target_phrase, config)

        if not name:
            tray.set_state("error", f"unknown: {target_phrase}")
            window.set_state("error", f"unknown: {target_phrase}")
            reply = f"I don't have '{target_phrase}' set up. Add it to config.json first."
            voice.say(reply)
            window.log_line(f"Nova: {reply}")
            return

        tray.set_state("executing", f"open {name}")
        window.set_state("executing", f"open {name}")
        ok = execute(kind, path)
        reply = (
            f"Opening {name}."
            if ok
            else f"I found {name} in my config, but couldn't open it. Check the path in config.json."
        )
    else:
        reply = "I only know how to open apps and folders right now."

    voice.say(reply)
    window.log_line(f"Nova: {reply}")
    tray.set_state("idle")
    window.set_state("idle")


def main():
    config = load_config()
    voice_enabled = config.get("voice_enabled", False)

    listener = None
    if voice_enabled:
        # Imported lazily so text-only mode never needs sounddevice/vosk
        # installed or a microphone present.
        from speech import SpeechListener

        model_path = config.get("vosk_model_path", "model")
        print(f"[Nova] Loading speech model from '{model_path}'... (this can take a few seconds)")
        listener = SpeechListener(model_path)

    voice = Voice()
    brain = Brain(config)

    should_quit = threading.Event()
    window = TranscriptWindow(
        config.get("agent_name", "Nova"),
        on_quit=should_quit.set,
        text_input=not voice_enabled,
    )

    tray = StatusTray(on_quit=lambda: (should_quit.set(), window.request_quit()))
    tray.run_in_background()

    if voice_enabled:
        thread = threading.Thread(
            target=voice_loop,
            args=(config, listener, voice, brain, tray, window, should_quit),
            daemon=True,
        )
    else:
        thread = threading.Thread(
            target=text_loop,
            args=(config, voice, brain, tray, window, should_quit),
            daemon=True,
        )
    thread.start()

    # tkinter's mainloop must run on the thread that created the Tk root.
    window.run()
    should_quit.set()


if __name__ == "__main__":
    main()
