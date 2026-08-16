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
`tasks` dict for lifecycle tracking. `SessionState` itself is explicitly in-memory
only, gone on restart — durable memory (below) is deliberately a separate, narrower
mechanism rather than persisting that whole log. Seven tools are always exposed to the
model, plus two more conditional groups (`remember`; five `discord_*` tools):
- `open_item` → `actions.find_target`/`execute` directly.
- `list_documents` → `subagents.list_documents`, which lists the readable files inside
  one (or all) of `config.json["folders"]`, so the model can discover filenames instead
  of requiring the user to already know them.
- `read_document` → `subagents.run_document_agent`, which resolves each target file
  (up to `MAX_FILES_PER_CALL`) against `config.json["folders"]` (with an explicit
  path-traversal guard — realpath + commonpath check, so `..`/symlink tricks can't
  escape the whitelisted folder), extracts text (`.txt`/`.md` directly, `.pdf` via
  `pypdf`, `.docx` via `python-docx`, `.csv` via stdlib `csv`, `.xlsx` via `openpyxl`,
  each file capped at a share of `MAX_CHARS`), then runs a **second, separate,
  tool-less** GPT call scoped only to that content — a sub-agent with zero system
  access of its own. Multiple files are concatenated with `=== filename ===` section
  headers in one call, so instructions can compare/cross-reference them.
- `download_file` → `subagents.download_file`, the project's **only write path** — it's
  deliberately more locked down than the read-only tools: only `http`/`https` URLs
  (rejects `file://`, `ftp://`, etc.), a `BLOCKED_DOWNLOAD_EXTENSIONS` denylist for
  executables/scripts (`.exe`/`.bat`/`.ps1`/`.dll`/etc.), a `MAX_DOWNLOAD_BYTES` (50MB)
  cap enforced both from `Content-Length` and while streaming (so a lying/missing header
  can't bypass it), a filename sanitizer (`_safe_filename`) that strips path separators
  and invalid characters so a crafted name can't escape the folder or collide with `..`,
  and `_unique_path` which always appends `(1)`, `(2)`, etc. rather than overwriting an
  existing file. Streams to a `.part` file and only `os.replace`s it into place on
  success, so a failed/oversized download never leaves a corrupt or half-written file
  under its real name.
- `launch_coding_agent` → `coding_agent.launch_coding_agent`, which resolves a repo from
  `config.json["repos"]` (same substring-match style as the other whitelist lookups) and
  launches the **Claude Code CLI** in a brand-new, visible console window via
  `subprocess.Popen([comspec, "/c", claude_path], cwd=root,
  creationflags=subprocess.CREATE_NEW_CONSOLE)` — never `shell=True`, and `repo_name` is
  only ever used as a dict-key lookup, never concatenated into a command string, so
  there's no injection surface. This is deliberately the same shape as `open_item`:
  Nova is only ever "launching a pre-approved thing," never gaining file access itself.
  What makes it different is what it launches — Claude Code has full read/write access
  to whatever repo it's pointed at, gated entirely by *its own* permission system, not
  Nova's. This is why `config["repos"]` is its own whitelist category rather than reusing
  `folders`: adding a repo here is a materially bigger trust decision than adding a
  folder Nova can merely read files from. No task-seeding in this version — passing
  dynamic text into a `.cmd`-based CLI launch via `cmd /c` risks cmd.exe's own
  metacharacter handling (`%`/`&`/`|`/`^`), so the session opens empty and the user
  types into it directly; a headless/report-back version (likely via the Claude Agent
  SDK rather than shelling out) is deferred, tracked as a possible v2.
- `set_reminder` → handled inline in `Brain` (no sub-agent GPT call needed): the model
  computes `delay_seconds` itself using the current local time injected into the system
  prompt each turn (`Brain._build_system_prompt`, rebuilt per `respond()` call rather
  than cached, so a long-running session doesn't reason from a stale clock), then
  `Brain._run_set_reminder` starts a daemon `threading.Timer` and returns immediately.
  Delivery happens later, out-of-band from any GPT turn, via `Brain.on_reminder` — a
  callback `main.py` wires to `voice.say`/`window.log_line`, the same pattern as
  `on_agent_event`. In-memory only; lost if Nova restarts before it fires.
- `web_search` → OpenAI's **hosted** tool (`{"type": "web_search"}` in `TOOLS`), not a
  custom function — OpenAI runs the search itself inside the same `responses.create()`
  call, so `Brain` never "invokes" it the way it invokes the function tools above. It's
  reported to the dashboard after the fact: `Brain._emit_web_search_events` scans
  `response.output` for `web_search_call` items post-call and emits the `lookup_agent`
  running/done pair retroactively. This tool sits outside the whitelist gate by
  design — it never touches the local filesystem or apps, only outbound web
  lookups via OpenAI, so the gate's purpose doesn't apply to it.
- `remember` → `memory.py`, the codebase's other write path alongside
  `subagents.download_file`, and narrower still: it always writes to the single
  exact path in `config.json["memory_path"]` — the model never supplies a
  filename, so (unlike `read_document`) there's no traversal surface to guard
  against — and every write is **append-only** (`memory.append_memory` only ever
  opens in `"a"` mode), so a bad tool call can add a stray note but can never
  edit or delete one already saved. Only offered to the model at all when
  `config["memory_enabled"]` is true (`Brain.__init__` builds `self.tools` from
  the base `TOOLS` list plus `REMEMBER_TOOL` conditionally) — off by default,
  same explicit-opt-in treatment as `voice_enabled`/`tts_enabled`. There's no
  `recall`/search tool: `Brain._build_system_prompt` just folds
  `memory.read_memory()`'s content (capped at `MAX_MEMORY_CHARS`, keeping the
  most recent entries) straight into the system prompt every turn, the same
  way `_describe_whitelist` already does for apps/folders — deliberately not
  wired up as a visible dashboard agent, since recalling memory isn't Nova
  delegating an external action the way the other tools are, it's closer to
  the silent short-term memory `SessionState.messages` already provides.
- `discord_send_message`/`discord_create_channel`/`discord_delete_channel`/
  `discord_rename_channel`/`discord_delete_last_bot_message` → `discord_agent.py`'s
  `DiscordAgent`, Nova's third input/output channel (voice, dashboard, now Discord).
  Two hard gates, both enforced in code rather than left to model judgment:
  `discord_agent._is_authorized` checks the message author's ID against
  `config["discord_owner_id"]` **before** the text ever reaches `Brain.respond` — a
  DM or a message in `config["discord_guild_id"]` from anyone else is silently
  ignored — and every management action resolves its target only inside that one
  guild (`DiscordAgent._get_guild`), never anywhere else the bot might be added.
  Only offered to the model when `config["discord_enabled"]` is true, same
  conditional-tools pattern as `REMEMBER_TOOL`. Architecturally the one new pattern
  here: `discord.py`'s bot runs its own asyncio event loop on a dedicated thread
  (`DiscordAgent.run`, started from `main.py` the same way `voice_loop`/`text_loop`
  are), but `Brain._run_discord`'s handlers can be called from a *different* thread
  (whenever the command originated locally, not from Discord) — every
  `DiscordAgent` management method bridges this via
  `asyncio.run_coroutine_threadsafe(coro, self.client.loop).result(timeout=...)`
  rather than assuming it's already on the bot's own loop. Deliberately excludes
  kick/ban/role management — not just unwhitelisted, the tools don't exist — since
  there's no way to delegate that to a separately-gated tool the way
  `launch_coding_agent` does for coding, and the failure mode (removing a real
  person) is worse than anything reversible like a deleted channel. The bot's own
  Discord-granted permissions (see `README.md`) are real defense in depth here: if
  the token itself ever leaked, `_is_authorized` wouldn't be what stops misuse —
  only the permissions actually granted when the bot was invited would.

`brain.py`'s `AGENTS` list — `(id, display_label, color)` tuples, e.g.
`("brain","Nova","#b46bff")` — is the single source of truth for the characters the
dashboard draws, passed into `TranscriptWindow` so the UI layer never imports
`brain.py`. `dashboard.html`'s `initAgents()` builds every non-brain agent's room div
and connector line dynamically from this list (only `room-brain` is static HTML,
being the fixed root), so adding another agent (e.g. `coding_agent`/Cody) is a one-line
Python change with no HTML/JS edits required. The only coupling between orchestrator
and UI is
`Brain.on_agent_event(event)`, a callback `main.py` wires to `window.handle_agent_event`
— every `thinking`/`running`/`done`/`error` transition flows through it.

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

**Whitelist population is deliberately out-of-band from the agent.** `discovery.py`
(`_app_paths_from_registry` via stdlib `winreg`, `_start_menu_shortcuts` via one
batched PowerShell `WScript.Shell` call to resolve `.lnk` targets) and
`discover_apps.py` (the interactive CLI built on it) exist purely to make
`config.json` faster to populate by hand — neither `brain.py` nor `actions.py`
imports `discovery.py`, so nothing this module finds is reachable by Nova/GPT until
a human runs the script and explicitly picks entries into `config.json`. This keeps
`actions.py`'s stated invariant true: "Nova can't add to its own whitelist."
