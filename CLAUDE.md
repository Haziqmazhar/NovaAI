# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Nova: a Windows desktop assistant with a hard security boundary — it can only ever
open apps/folders or read files that the user explicitly pre-approved in `config.json`.
An optional GPT-powered orchestrator sits on top of that boundary to interpret
open-ended requests, but it never widens what's actually allowed to happen.

## Commands

```powershell
# Setup
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run
python main.py

# The only automated test — standalone script, no pytest, no API key/network needed
python test_actions_logic.py
```

No lint/format tooling is configured (no pyproject.toml/setup.cfg/ruff/flake8) and
no build step exists — it's a plain script run via `python main.py`.

To exercise the GPT-powered path, set `OPENAI_API_KEY` (env var) before running.
Without it, `Brain.enabled` is `False` and everything transparently falls back to
offline pattern matching — this fallback also fires automatically if the API call
raises for any reason (bad key, quota, network), so it's safe to test both paths
just by toggling the env var.

### Testing changes to `brain.py`

There's no committed test for the orchestrator's tool loop, but the pattern used
during development (and worth reusing) is: mock `client.responses.create` to
return fake objects with `.output` (a list of items with `.type` — either
`"function_call"` objects exposing `.name`/`.arguments`/`.call_id`, or anything
else to represent a final turn) and `.output_text`, then assert on the exact
sequence of `on_agent_event` calls (e.g. `[("brain","thinking"), ("app_agent","running"),
("app_agent","done"), ("brain","thinking"), ("brain","idle")]`) and on
`Brain.state.messages` accumulation. This exercises the full loop without hitting
the network. Follow up with one real call against a live key before trusting it.

## Architecture

**Everything funnels through one whitelist gate.** `actions.py::find_target` fuzzy-matches
spoken/typed text against `config.json`'s `apps`/`folders` dicts; `execute()` is the
only function in the codebase that touches the OS (`subprocess.Popen` / `os.startfile`).
Both command-handling paths below — the offline one and the GPT one — call into this
same gate. There is no code path that runs an arbitrary string as a shell command.

**Two parallel command-handling paths**, chosen per-command in `main.py::handle_command`
based on `brain.enabled`:
- **Offline** (`handle_command_offline`): literal `"open X"` substring parsing. Always
  available, stdlib only.
- **GPT-powered** (`brain.py::Brain.respond`): an orchestrator loop against OpenAI's
  **Responses API** (`client.responses.create` — not the older Chat Completions API;
  this is a meaningfully different request/response shape and the current recommended
  surface). Any exception here is caught in `main.py` and falls through to the offline
  path, so a bad key, exhausted quota, or network failure degrades gracefully instead
  of crashing.

**Orchestrator + sub-agent pattern** (`brain.py` + `subagents.py` + `session_state.py`):
`Brain` holds a `SessionState` — a persisted `messages` list (the Responses API `input`
items, accumulated across the whole process lifetime for multi-turn memory) and a
`tasks` dict for lifecycle tracking. Two tools are exposed to the model:
- `open_item` → `actions.find_target`/`execute` directly.
- `read_document` → `subagents.run_document_agent`, which resolves the target file
  against `config.json["folders"]` (with an explicit path-traversal guard — realpath
  + commonpath check, so `..`/symlink tricks can't escape the whitelisted folder),
  extracts text (`.txt`/`.md` directly, `.pdf` via `pypdf`, capped at `MAX_CHARS`),
  then runs a **second, separate, tool-less** GPT call scoped only to that file's
  content — a sub-agent with zero system access of its own.

`brain.py`'s `AGENTS` list (`[("brain","Nova"), ("app_agent","Appy"), ("document_agent","Duc")]`)
is the single source of truth for the 3 characters the dashboard draws, passed into
`TranscriptWindow` so the UI layer never imports `brain.py`. The only coupling between
orchestrator and UI is `Brain.on_agent_event(event)`, a callback `main.py` wires to
`window.handle_agent_event` — every `thinking`/`running`/`done`/`error` transition
flows through it.

**The dashboard is pywebview + one self-contained HTML file, not tkinter** (an earlier
tkinter/customtkinter version was fully replaced this project). `transcript_window.py`
is a thin Python shim: it reads `dashboard.html` once and opens it in a native,
chrome-less window (`webview.create_window(html=...)`, WebView2 on Windows). Outbound
updates (Python → JS) go straight through `window.evaluate_js(...)` — confirmed safe
to call from a background thread while `webview.start()` blocks the main thread, so
there's no queue on this side. Inbound (JS → Python, typed commands) goes through a
`js_api` object (`_Api.submit_command`) that pushes onto a `queue.Queue`, which
`get_command(timeout)` polls from the command loop's own thread — this direction does
need the queue, since it crosses from pywebview's internal thread to Nova's command
thread. `dashboard.html` owns all visual/animation logic itself (inline `<style>`/
`<script>`, no external assets, no build step, no other file defines any of the
character/animation design).

**Threading** (`main.py::main`): the command loop (`voice_loop` or `text_loop`,
selected by `config["voice_enabled"]`) runs on a background daemon thread;
`window.run()` (pywebview's blocking call) runs on the main thread — required because
GUI event loops must own their creating thread.

**Two independent, fully-optional offline toggles in `config.json`** (both default
`false`):
- `voice_enabled` — off: no Vosk, no `sounddevice`, no mic; commands come from the
  dashboard's floating input bar instead. `speech.py` is imported lazily inside
  `main()`, only when this is `true`, so text-only usage never needs that dependency
  chain installed or working.
- `tts_enabled` — off: `tts.py::Voice` never initializes the pyttsx3 engine; replies
  still print/log normally, Nova just stays silent.
