# Nova — a voice-activated desktop agent (v2)

Nova listens for the wake word **"hey nova"**, then opens whatever whitelisted
app or folder you ask for — e.g. *"hey nova, open chrome"*. Speech recognition
(Vosk) and text-to-speech (pyttsx3) both run **offline**, no API key required.

Nova now optionally routes commands through **GPT (gpt-5-mini)** so it can
hold a normal conversation and understand open-ended phrasing, not just exact
"open X" commands. GPT gets two tools — `open_item` and `read_document` —
wired straight into the same whitelist gate `actions.py` always enforced, so
it can still only ever touch apps/folders/files you've pre-approved in
`config.json`. If you don't set an API key, Nova falls back to the original
offline pattern matching automatically — nothing breaks.

A small always-on-top **transcript window** now shows what Nova heard and
said, alongside the tray icon's idle/listening/executing status.

---

## 1. Prerequisites

- Windows 10 or 11
- [Python 3.10+](https://www.python.org/downloads/) — when installing, check
  "Add python.exe to PATH"
- A working microphone

## 2. Set up the project

Open **PowerShell** in this folder (`nova_agent`) and run:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

If `pyaudio`/`sounddevice` fails to install, you may need the
[Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) —
this is a known Windows/Python quirk unrelated to Nova itself. Tell me the
exact error and I'll help you work around it.

## 3. Download the speech model (optional while `voice_enabled` is false)

`config.json` currently ships with `"voice_enabled": false` — Nova takes
typed commands in the transcript window instead of listening on the mic,
which is useful while iterating on the agent/display without touching
audio. You can skip this step and step 4's model folder entirely until
you set `"voice_enabled": true`.

It also ships with `"tts_enabled": false`, so Nova stays quiet and just
prints/logs its replies instead of speaking them aloud — set it to `true`
whenever you want to hear it again.

Nova needs a Vosk speech model to understand you. Download the small English
model (about 40MB) from:

https://alphacephei.com/vosk/models

Get **`vosk-model-small-en-us-0.15`**, unzip it, and rename the unzipped
folder to `model`, placing it directly inside `nova_agent/` — so you end up
with `nova_agent/model/` containing files like `am`, `conf`, `graph`, etc.

(If you'd rather keep the original folder name, just update
`vosk_model_path` in `config.json` to point at it.)

## 4. (Optional) Enable the GPT-powered brain

Nova works fully offline without this step — skip it if you just want the
original "open X" behavior.

To let Nova understand open-ended requests and hold a conversation:

1. Get an API key from the [OpenAI Platform](https://platform.openai.com/api-keys)
   (this requires setting up billing — see cost notes below).
2. Set it as an environment variable before running Nova:

   ```powershell
   $env:OPENAI_API_KEY = "sk-proj-..."
   python main.py
   ```

   To make this permanent, set it as a Windows user environment variable
   instead (Settings → System → About → Advanced system settings →
   Environment Variables) so you don't have to re-set it every session.

**Cost:** Nova uses `gpt-5-mini`, one of OpenAI's cheapest tiers. A typical
short command is a few hundred tokens — for personal, occasional use this
comes out to well under $1/month.

**Privacy:** with a key set, your spoken commands (as text) are sent to
OpenAI's API for that one request. Per OpenAI's standard API terms, this
data isn't used to train models by default (opt-in only) and is retained
only briefly (~30 days) for abuse monitoring — but it does leave your
machine, unlike everything else in Nova. If you'd rather keep everything
fully local, don't set the key (or set `"use_ai_brain": false` in
`config.json`) and Nova runs exactly as in v1.

## 5. Install/upgrade dependencies

If you set up `venv` before this update, re-run `pip install -r requirements.txt`
to pick up the `openai` package (needed only if you enabled the brain in
step 4 — harmless either way).

## 6. Configure your whitelist

Open `config.json`. This file is the **only** thing that controls what Nova
is allowed to open — edit it to match your actual installed apps and folder
paths:

- Replace `%USERNAME%` entries — Windows will expand this automatically, but
  double check paths like the Chrome install location match your machine.
- Add or remove entries under `"apps"` and `"folders"` freely. The key
  (e.g. `"chrome"`) is the word you'll say; the value is the full path to
  the `.exe` or folder.
- Don't add anything to this file you wouldn't want opened by voice.

## 7. Run it

```powershell
python main.py
```

You should see a tray icon appear (gray = idle) plus a small transcript
window, and hear "Nova is online." Say **"hey nova"**, wait for it to say
"Yes?", then say something like **"open chrome"**. The tray icon turns blue
while listening and green while executing; the transcript window logs what
Nova heard and said.

Quit anytime with **Ctrl+C** in the terminal, closing the transcript window,
or right-click the tray icon → **Quit Nova**.

## 8. (Optional) Launch Nova automatically on boot

The safest, most reversible way to do this on Windows:

1. Press `Win + R`, type `shell:startup`, hit Enter — this opens your
   personal Startup folder.
2. Right-click inside it → **New → Shortcut**.
3. Point it at `run_nova.bat` inside this project folder.
4. Name it "Nova" and finish.

Now Nova launches (in a terminal window) whenever you log in. You can remove
it anytime by deleting that shortcut — nothing else on your system is
touched.

*(I deliberately didn't auto-create this shortcut via script — modifying
your Startup folder is exactly the kind of thing you should be able to see
and undo by hand, at least while you're still trusting this project.)*

---

## Security notes (please read)

- Nova can **only** open things listed in `config.json`. There is no code
  path — not even through GPT — that takes what you say and runs it as a
  raw shell command. GPT (when enabled) has exactly two tools, `open_item`
  and `read_document`, and each calls straight into an explicit whitelist
  check (`find_target`/`execute` for apps/folders, a folder-boundary check
  in `subagents.py` for files). GPT can suggest calling a tool; it cannot
  widen what the tool is allowed to do.
- Nova does not have file *editing*, deletion, or browsing capabilities —
  it can only launch apps/folders, or read files, that you've pre-approved.
- Speech-to-text and text-to-speech always happen locally, regardless of
  whether the GPT brain is enabled.
- **With `OPENAI_API_KEY` set**, the text of your commands (not raw audio)
  is sent to OpenAI's API to generate a response. This is the one place
  data leaves your machine. Without the key set (or with `"use_ai_brain":
  false` in `config.json`), Nova is exactly as offline as v1. See step 4
  above for the cost/privacy tradeoff.

## Where to go from here

Natural next steps, roughly in order of difficulty:

1. **More command types** — beyond "open X", you could add "close X" (kill
   a process), "search for X" (open a browser search), volume control, etc.
   With the GPT brain enabled, the more natural extension is another tool
   (e.g. `close_item`) rather than another `if` branch — GPT will pick the
   right tool from context instead of needing exact phrasing.
2. **Multi-turn memory** — `brain.py` currently sends one user turn per
   command with no history. Keeping a short rolling conversation would let
   you say "actually, open my documents instead" and have Nova understand
   the follow-up.
3. **Full computer-use / screen control** (clicking, typing into other
   apps) — this is a much bigger step up in both complexity and risk.
   Rather than building this from scratch, this is a good point to
   evaluate an existing open-source project like **Open Interpreter**
   (which has an explicit "OS mode" for this) and decide whether to
   integrate it or keep building your own.

Bring me whatever errors or behavior you see when you run this, and we'll
iterate from there.
