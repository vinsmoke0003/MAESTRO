# Getting Started with MAESTRO

From a fresh `git clone` to talking to MAESTRO on a new computer, step by step,
for **macOS** and **Windows**. Linux follows the macOS commands.

Setup takes about 10 minutes, most of it downloading packages. Everything
MAESTRO needs is free.

---

## What you need first

| Thing | Why | Get it |
|---|---|---|
| **Git** | to download the code | macOS: comes with Xcode tools (`xcode-select --install`). Windows: <https://git-scm.com/download/win> |
| **Python 3.12** (3.11 or 3.13 also work) | MAESTRO is written in Python | <https://www.python.org/downloads/> |
| A microphone | only for voice (`maestro voice`) | any built-in or USB mic |
| Ollama | optional: a local AI planner | <https://ollama.com> |
| A Google account | optional: Gmail, Drive, Calendar | see step 8 |

> **Use Python 3.11, 3.12 or 3.13.** Python 3.10 is too old for the trained
> intent model, and Python 3.14 does not yet have the speech engine on macOS.
> On Windows, tick **"Add python.exe to PATH"** in the installer.

Check what you have:

**macOS**
```bash
python3.12 --version
```

**Windows (PowerShell)**
```powershell
py -3.12 --version
```

If that prints `Python 3.12.x`, you are ready.

---

## 1. Download the code

```bash
git clone https://github.com/vinsmoke0003/MAESTRO.git
```

```bash
cd MAESTRO/Project
```

Everything below is run from inside the `Project` folder.

---

## 2. Create a virtual environment (venv)

A venv is a private folder of Python packages just for MAESTRO, so it never
clashes with anything else on the computer. You create it once.

**macOS / Linux**
```bash
python3.12 -m venv .venv
```

**Windows (PowerShell)**
```powershell
py -3.12 -m venv .venv
```

This makes a `.venv` folder inside `Project`. It is gitignored, so it never
gets committed.

---

## 3. Activate the venv

You do this **every time you open a new terminal** to use MAESTRO.

**macOS / Linux**
```bash
source .venv/bin/activate
```

**Windows (PowerShell)**
```powershell
.venv\Scripts\Activate.ps1
```

**Windows (Command Prompt)**
```bat
.venv\Scripts\activate.bat
```

Your prompt now starts with `(.venv)`. That is how you know it is active.

> **Windows: "running scripts is disabled on this system"?** Run this once,
> then activate again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

---

## 4. Install everything

```bash
python -m pip install --upgrade pip
```

```bash
pip install -r requirements.txt
```

This installs MAESTRO and everything it uses: the safety layer, the trained
intent model, voice, Gmail/Drive/Calendar support and the test tools. It also
creates the `maestro` command. See [`Project/requirements.txt`](Project/requirements.txt)
for what each package is for.

---

## 5. Check it works

```bash
maestro doctor
```

`doctor` tests each feature on this machine and marks it `[READY]` or
`[MISSING]`, with the reason. Things like the browser or Google showing
`MISSING` at this point is normal: they are optional (steps 7–8).

Run the test suite (about 30 seconds, no internet needed):

```bash
python -m pytest -q
```

You should see all tests pass, with 9 skipped (the browser tests, until you do
step 7).

---

## 6. Run MAESTRO

### Type a command

```bash
maestro ask "how much disk space is left"
```

```bash
maestro ask "list all the pdfs in my downloads"
```

```bash
maestro ask "move the pdfs from Downloads to Documents/Invoices"
```

Anything that changes files shows a preview first and asks `Approve? [y]es / [n]o`.
Looking at files never asks.

### Preview only (never executes)

```bash
maestro plan "move the screenshots on my desktop to Pictures"
```

### The guided demo

```bash
maestro demo
```

A scripted tour: a real task, a refusal, a clarifying question, a
prompt-injection attempt, and the audit log check.

### The web interface

```bash
maestro ui
```

Opens `http://127.0.0.1:8765` in your browser: type an instruction, see the
preview with its risk level, approve or deny, watch progress, undo.

### Talk to it

```bash
maestro voice
```

Wait for *"MAESTRO is listening"*, then start each command with its name:

- *"Maestro, how much disk space is left?"*
- *"Maestro, list all the PDFs in my downloads."*
- *"Goodbye Maestro"* (or `Ctrl + C`) to stop.

The first run downloads the speech model once (~145 MB), then works offline.

If your room is noisy or it does not catch you, use push-to-talk. Press
**Enter**, speak, press **Enter** again:

```bash
maestro voice --push-to-talk
```

Test the microphone on its own:

```bash
maestro voice --mic-test
```

> **Microphone permission (macOS).** The first time, macOS asks whether your
> terminal app may use the microphone. Click **Allow**. Run voice from
> **Terminal**, **iTerm** or the **VS Code** terminal. Some embedded terminals
> (for example inside other desktop apps) cannot get microphone access; the
> mic test tells you within 5 seconds if that is the case. You can change this
> later in **System Settings → Privacy & Security → Microphone**.

### Undo the last task

```bash
maestro undo
```

### Other useful commands

| Command | What it does |
|---|---|
| `maestro verbs` | every action MAESTRO can take, and its risk level |
| `maestro audit` | verify the tamper-evident log of everything it did |
| `maestro episodes` | usage history |
| `maestro --help` | all commands |

---

## 7. Optional extras

### Browser tasks (open pages, extract text, download)

```bash
pip install playwright
```

```bash
playwright install chromium
```

About 150 MB. Afterwards the 9 skipped browser tests run too.

### A local AI planner (smarter understanding, still free)

Install Ollama from <https://ollama.com>, then:

```bash
ollama pull qwen2.5:7b-instruct-q4_K_M
```

MAESTRO detects it automatically whenever Ollama is running. Without it,
MAESTRO uses its built-in rule-based planner, which works offline.

---

## 8. Connect Gmail, Google Drive and Google Calendar (optional)

About 10 minutes, once, and free. You create your own Google Cloud "app" and
sign in with your own account. Full walkthrough:
[`Project/docs/GOOGLE-SETUP.md`](Project/docs/GOOGLE-SETUP.md).

In short:

1. In <https://console.cloud.google.com>, create a project called `MAESTRO`.
2. Enable **Gmail API**, **Google Drive API** and **Google Calendar API**.
3. Set up the OAuth consent screen as **External** / **Testing**, and add your
   own Gmail address as a test user.
4. Create an OAuth client of type **Desktop app** and download its JSON.
5. Put it where MAESTRO looks for it:

**macOS / Linux**
```bash
mkdir -p ~/.maestro/google && mv ~/Downloads/client_secret_*.json ~/.maestro/google/client_secret.json
```

**Windows (PowerShell)**
```powershell
New-Item -ItemType Directory -Force "$HOME\.maestro\google"; Move-Item "$HOME\Downloads\client_secret_*.json" "$HOME\.maestro\google\client_secret.json"
```

6. Sign in:

```bash
maestro google connect
```

Google warns *"Google hasn't verified this app"*. That is expected for your
own app: click **Continue**, then approve.

Then try:

```bash
maestro ask "check my inbox"
```

```bash
maestro ask "check my last 15 emails for tests or tickets and add reminders to my calendar"
```

MAESTRO never sends email (it saves drafts for you to send), never shares or
deletes Drive files, and shows every calendar event for your approval before
adding it.

---

## 9. Settings (optional)

Everything works with the defaults. To change something, set an environment
variable; all of them are listed with explanations in
[`Project/.env.example`](Project/.env.example).

| Variable | Default | Meaning |
|---|---|---|
| `MAESTRO_WORKSPACE` | `~/maestro_workspace` | MAESTRO's own working folder |
| `MAESTRO_HOME` | `~/.maestro` | audit log, history, Google sign-in |
| `MAESTRO_LLM` | `auto` | `auto`, `ollama` or `none` |
| `MAESTRO_STT_MODEL` | `base.en` | `tiny.en` (faster) or `small.en` (more accurate) |

---

## 10. Every time after the first

Open a terminal, then:

**macOS / Linux**
```bash
cd MAESTRO/Project && source .venv/bin/activate
```

**Windows (PowerShell)**
```powershell
cd MAESTRO\Project; .venv\Scripts\Activate.ps1
```

Then any `maestro ...` command. To leave the venv:

```bash
deactivate
```

To get the latest code later:

```bash
git pull
```

```bash
pip install -r requirements.txt
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `maestro: command not found` | The venv is not active. Do step 3 (look for `(.venv)` in the prompt). |
| `No matching distribution found` while installing | Wrong Python version. Delete `.venv` and redo step 2 with Python 3.12. |
| `python3.12: command not found` (macOS) | Install Python 3.12 from python.org, or use `python3.11` / `python3.13`. |
| Voice stays on "listening" and never hears you | Run `maestro voice --mic-test`. Allow microphone access for your terminal app, or use `--push-to-talk`. |
| `PaMacCore ... err='-50'` after Ctrl+C | Harmless: the audio system noticing the mic was closed. |
| Voice replies are silent on Linux | Install a speech engine: `sudo apt install espeak-ng`. |
| Google commands say "run maestro google connect" | The Google sign-in expires about weekly while your app is in Testing mode. Run it again. |
| Browser tasks report "not available" | Do step 7 (Playwright + Chromium). |
| Tests fail right after cloning | Make sure you are inside `Project` and the venv is active, then `pip install -r requirements.txt` again. |

---

## Where to read more

| File | What it covers |
|---|---|
| [`README.md`](README.md) | the project, results, and the safety model |
| [`Project/README.md`](Project/README.md) | the developer guide and full results discussion |
| [`Project/docs/GOOGLE-SETUP.md`](Project/docs/GOOGLE-SETUP.md) | connecting Gmail, Drive, Calendar |
| [`Project/docs/README.md`](Project/docs/README.md) | where each part of the specification lives in the code |
| [`docs/`](docs) | the original specification: requirements, architecture, safety, evaluation |
