# Nova — a voice-activated desktop agent (v2)

Nova listens for the wake word **"hey nova"**, then opens whatever whitelisted
app or folder you ask for — e.g. *"hey nova, open chrome"*. Speech recognition
(Vosk) and text-to-speech (pyttsx3) both run **offline**, no API key required.

Nova now optionally routes commands through **GPT (gpt-5-mini)** so it can
hold a normal conversation and understand open-ended phrasing, not just exact
"open X" commands. GPT gets seven tools by default — `open_item`,
`read_document`, `list_documents`, `download_file`, `launch_coding_agent`,
`set_reminder`, and `web_search` — wired straight into the same whitelist
gate `actions.py` always enforced, so it can still only ever touch
apps/folders/files/repos you've pre-approved in `config.json`. Two more are
off by default and opt-in: `remember` (see **Give Nova persistent memory**)
and five Discord server-management tools (see **Give Nova a Discord
channel**). `download_file` and `remember` are Nova's only capabilities that
**write** to disk; `launch_coding_agent` and the Discord tools are the ones
where Nova's *own* restrictions aren't the only backstop — see **Security
notes** below for the full breakdown of each. `set_reminder` and `web_search`
sit outside the whitelist gate entirely, by nature rather than by oversight:
`set_reminder` never touches the filesystem at all (it's just a background
timer), and `web_search` never touches your machine's files/apps either (it's
outbound web lookups via OpenAI). If you don't set an API key, Nova falls
back to the original offline pattern matching
automatically — nothing breaks.

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
comes out to well under $1/month. `web_search` (Scout) adds a small per-search
tool-call cost on top of that when it's actually used — still negligible at
personal-use volume, but worth knowing it's a separate line item from token
cost on OpenAI's usage page.

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

**Optional: auto-discover installed apps.** Instead of hand-typing every path,
run:

```powershell
python discover_apps.py
```

It scans your Start Menu shortcuts and the registry's `App Paths` key for
apps not already in `config.json`, lists the ones it finds, and lets you pick
which to add and under what name. It backs up `config.json` to
`config.json.bak` first, then writes the new entries in directly — restart
Nova afterward to pick up the change. This is a script you run yourself; it's
not a Nova/GPT capability, so it doesn't change what Nova can do on its own —
see **Security notes** below.

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

## 9. (Optional) Give Nova persistent memory

By default Nova forgets everything when it restarts — even with the GPT
brain on, conversation history only lasts one running session. To let it
remember facts about you (preferences, names, ongoing context) across
restarts, set in `config.json`:

```json
"memory_enabled": true,
"memory_path": "memory.md"
```

`memory_path` can be any markdown file — a relative name resolves inside
the project folder, or point it at an absolute path inside your **Obsidian**
vault (e.g. `"C:\\Users\\you\\Documents\\YourVault\\Nova\\memory.md"`) to
keep memories there instead, as an ordinary note you can open, read, or
edit by hand.

Once enabled, say things like *"remember that I take my coffee black"* and
Nova appends a note; it's folded back into every future conversation
automatically — no need to ask it to "recall" anything. It's append-only
and opt-in on purpose: see **Security notes** below.

## 10. (Optional) Launch a real coding agent on a repo

Nova can open **Claude Code** — a separate, fully capable AI coding agent,
not Nova itself — in a new terminal window, scoped to one repo you've
whitelisted. This requires the Claude Code CLI installed separately (see
Anthropic's docs) with `claude` on your PATH; Nova checks for this and tells
you plainly if it's missing rather than failing silently.

Add repos under a new `"repos"` section in `config.json`, same shape as
`"apps"`/`"folders"`:

```json
"repos": {
  "nova": "C:\\path\\to\\your\\repo"
}
```

Then say *"open Claude Code in the nova repo"* (or similar) and a new window
opens, already `cd`'d into that folder. Nova doesn't seed a task into it yet
— you type into the new window yourself once it's open. See **Security
notes** below for why this one is worth treating carefully.

## 11. (Optional) Give Nova a Discord channel

Nova can run a Discord bot as a third input/output channel alongside voice
and the dashboard — DM it (or message it in one whitelisted server) and it
responds the same way it would locally, plus it can manage that one server's
channels (create/rename/delete channels, send/delete messages).

**Set it up:**

1. Create an application + bot at the
   [Discord Developer Portal](https://discord.com/developers/applications).
2. Under **Bot**, enable the **Message Content Intent** (a privileged intent —
   without this the bot can't read message text at all).
3. Copy the bot token and set it as an environment variable:
   ```powershell
   $env:DISCORD_BOT_TOKEN = "your-token-here"
   ```
4. Under **OAuth2 → URL Generator**, pick the `bot` scope and only these
   permissions: **View Channels, Send Messages, Read Message History, Manage
   Messages, Manage Channels**. Do **not** grant Kick Members, Ban Members,
   Manage Roles, or Administrator — Nova's tools don't use them, and leaving
   them off the bot itself is real protection if the token ever leaks (see
   **Security notes**). Use the generated URL to invite the bot to your
   server.
5. In Discord, enable **Developer Mode** (User Settings → Advanced), then
   right-click your own name to **Copy User ID**, and right-click the server
   to **Copy Server ID**.
6. In `config.json`, set:
   ```json
   "discord_enabled": true,
   "discord_owner_id": "your user id",
   "discord_guild_id": "your server id"
   ```

Only messages from `discord_owner_id` are ever treated as commands — everyone
else's messages are visible to the bot but never acted on. This is a hard
check in code, not something GPT decides.

---

## Security notes (please read)

- Nova can **only** open or read things listed in `config.json`. There is no
  code path — not even through GPT — that takes what you say and runs it as
  a raw shell command. `open_item`, `read_document`, and `list_documents`
  each call straight into an explicit whitelist check (`find_target`/
  `execute` for apps/folders, a folder-boundary check in `subagents.py` for
  files). GPT can suggest calling a tool; it cannot widen what the tool is
  allowed to do.
- `download_file` and `remember` are Nova's only two capabilities that write
  anything to disk, and each carries its own extra guardrails on top of the
  usual whitelist:
  - `download_file` only accepts `http`/`https` URLs (no `file://` or other
    schemes), refuses executable/script extensions (`.exe`, `.bat`, `.ps1`,
    `.dll`, etc.) outright, caps downloads at 50MB, and never overwrites an
    existing file — a same-named download is saved as `name (1).ext`
    instead. It still can't write anywhere except a folder you've already
    whitelisted.
  - `remember` is off by default (`memory_enabled: false`) — when off, the
    tool doesn't exist as far as the model is concerned, not just
    "discouraged." When on, it always writes to the **one exact path** in
    `memory_path`, never a filename the model chooses, and it's
    **append-only** — it can add a note but can never edit or delete an
    existing one. If Nova ever gets something wrong, correct it by editing
    the memory file yourself; Nova has no tool to do that for you.
- `launch_coding_agent` (Cody) is Nova's **most powerful capability by far**
  — worth understanding clearly, not just trusting. Nova itself gets no new
  access from this feature: it only ever starts a process (`subprocess.Popen`,
  no `shell=True`, `repo_name` used purely as a whitelist dict-key lookup,
  never concatenated into a command string), the exact same pattern as
  `open_item` launching Chrome. But what it *starts* — Claude Code — has full
  read/write access to whatever repo you point it at, gated by Claude Code's
  own separate permission system, not Nova's. Only add repos under
  `"repos"` in `config.json` that you're genuinely comfortable an AI coding
  agent having full access to. There is deliberately no way for Nova to seed
  a task into that session yet (see step 10) — it opens empty, so there's no
  path for spoken/typed text to become a command-line argument at all.
- The **Discord bot** (Herald) is the second capability, after the coding
  agent, where Nova's own tool-level restrictions aren't the only backstop —
  the bot's actual Discord-granted permissions are too:
  - **Authorization is a hard, code-level gate.** `discord_agent.py`'s
    `_is_authorized` checks the message author's ID against
    `discord_owner_id` *before* anything reaches GPT — never an LLM judgment
    call. Everyone else's messages are visible to the bot (Discord requires
    that to route messages at all) but are never acted on.
  - **Every action is scoped to one server.** `discord_guild_id` is the only
    server Nova's tools will ever touch, even if the bot is later invited to
    others.
  - **Kick/ban/role management are deliberately excluded from v1**, not just
    left unwhitelisted — the tools don't exist. Deleting a channel or message
    is recoverable-ish; removing a real person from a community on a misfire
    isn't, and there's no way to delegate this one to a separately-gated real
    tool the way `launch_coding_agent` does for coding.
  - **The bot's own Discord permission grant is real defense in depth.** If
    the bot token itself ever leaked, Nova's code-level checks wouldn't help
    the attacker be stopped by Discord — only the permissions actually
    granted when you invited the bot would. That's why step 11 above asks you
    to grant only Manage Channels/Messages/Send/View/Read History, and
    explicitly not Administrator or membership permissions.
- `set_reminder` and `web_search` are deliberately **not** whitelist-gated —
  not an oversight, but because neither one touches your local apps or
  files, so the whitelist's purpose (bounding what Nova can do *to your
  machine*) doesn't apply to them:
  - `set_reminder` (Remy) is a plain in-memory background timer. It's lost
    if Nova restarts before it fires, same tradeoff already accepted for
    conversation history.
  - `web_search` (Scout) is OpenAI's hosted web-search tool — when it
    fires, your search query (and the page content it finds) round-trips
    through OpenAI's infrastructure, same as any other GPT-enabled request,
    just with live web results attached.
- Nova does not have file *editing* or deletion capabilities anywhere. It
  can list and read files inside folders you've pre-approved
  (`list_documents`, `read_document`), launch pre-approved apps/folders
  (`open_item`), save new downloaded files into a pre-approved folder
  (`download_file`), and — if you've opted in — append a note to its one
  memory file (`remember`). It can't browse outside those folders, modify
  an existing file, or delete anything, anywhere.
- `discover_apps.py` (see step 6) is a **script you run yourself**, not a
  Nova/GPT capability — `Brain` never imports `discovery.py`, so nothing it
  finds becomes usable until you explicitly pick it and it lands in
  `config.json`. Nova itself still can't add to its own whitelist.
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
   a process), volume control, etc. With the GPT brain enabled, the more
   natural extension is another tool (e.g. `close_item`) rather than another
   `if` branch — GPT will pick the right tool from context instead of
   needing exact phrasing, the same way `set_reminder` and `web_search`
   were added.
2. **Smarter document handling** — `read_document` truncates long files at a
   fixed character budget rather than chunking/summarizing them piece by
   piece, so very large documents only get a partial view today.
3. **Full computer-use / screen control** (clicking, typing into other
   apps) — this is a much bigger step up in both complexity and risk.
   Rather than building this from scratch, this is a good point to
   evaluate an existing open-source project like **Open Interpreter**
   (which has an explicit "OS mode" for this) and decide whether to
   integrate it or keep building your own.

Bring me whatever errors or behavior you see when you run this, and we'll
iterate from there.
