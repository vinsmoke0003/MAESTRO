# MAESTRO live demo (about 10 minutes)

A demo for a review or viva, built only from prompts that were run and checked
on the real app. The story it tells is the project's contribution: **MAESTRO
does useful desktop work, but nothing happens until you have seen exactly what
will happen, and anything risky can be refused, confirmed or undone.**

It uses a small demo folder with dummy files, so no personal file names appear
on the projector.

## Before the demo (5 minutes, once)

1. Create the demo folder with three dummy PDFs and one text file:

   ```bash
   mkdir -p ~/maestro_workspace/demo
   for n in invoice-march invoice-april receipt-may; do echo "dummy $n" > ~/maestro_workspace/demo/$n.pdf; done
   echo "meeting notes" > ~/maestro_workspace/demo/notes.txt
   ```

2. Start MAESTRO with the fast, deterministic template planner (instant plans;
   the local LLM takes about 20 s per plan, which is long on stage):

   ```bash
   MAESTRO_LLM=none maestro desktop
   ```

   The first time, install the window support with `pip install -e ".[desktop]"`.
   Or use `MAESTRO_LLM=none maestro ui`, which opens the same app in a browser.

3. Check the top bar says **MAESTRO ready** and **Audit log intact**.
4. Voice page → **Spoken replies → On** → **Test voice**. Set a comfortable volume.
5. Close notifications and other apps; set the display to mirror.

## The demo

| # | Say / do | What the audience sees | Point to make |
|---|---|---|---|
| 1 | Show the **Assistant** page | The five-step bar: Instruction → Preview → Approve → Run → Result | "Every request goes through the same five steps. Nothing skips the preview." |
| 2 | Type `what time is it` → **Preview plan** | Plan: 1 step, `sys.info`, risk **R0**, gate auto | "R0 means read-only. The risk is computed by deterministic rules, never by the language model." |
| 3 | **Approve and run** | "Done" and the time; MAESTRO **says** "It's … on …" | Spoken replies use the computer's own voice; nothing leaves the machine. |
| 4 | `how much disk space is left` → Preview → Approve | Free space; spoken "You have … gigabytes free …" | Same pipeline, different verb. |
| 5 | `move my files` → Preview plan | MAESTRO **asks** "Which folder should I look in?" Click **Cancel** | "When it is unsure, it asks instead of guessing." |
| 6 | `delete everything in Documents permanently` → Preview plan | **Refused**, spoken | "Permanent deletion is hard-blocked. There is no override, by design." |
| 7 | `Who is the President of Brazil?` → Preview plan | **Refused**: out of scope | "It is a desktop agent, not a chatbot." |
| 8 | `move the pdfs from ~/maestro_workspace/demo to ~/maestro_workspace/demo/archive` → Preview plan | 3 steps, risk **R2**, needs approval; dry run shows **3 files**; the table explains *why* each step got its risk | "This is the dry run: it simulated the move and counted the files before touching anything. R2 needs an explicit click." |
| 9 | **Approve and run** | "Done — 3 steps completed, 3 files touched"; the PDFs are now in `archive/`, `notes.txt` is untouched | Show the folder in Finder if you like. |
| 10 | **Activity** page → **Undo (verified)** on that run | "verified": all 3 PDFs back, each checked against the hash taken when it moved | "Reversible actions record a manifest with content hashes, so undo is verified, not hoped for." |
| 11 | Point at **Audit log: intact** | The event list (PROPOSED, EXECUTED, UNDONE…) | "Every step is in a hash-chained log. Editing any past row breaks the chain, and MAESTRO says so." |
| 12 | *Optional:* **Connections** page | Google: setup status; Connect only when set up | "Gmail is read-and-draft only; sending email is hard-blocked. Sign-in happens on Google's own page." |
| 13 | *Optional, rehearse first:* Voice → **Start recording**, say "what time is it", **Stop** → **Use in Assistant** | The words appear in the Assistant box; **nothing runs** | "Voice only fills the box. It can never approve a plan." |

Close with: *"The model proposes; deterministic code decides; the user consents;
everything is logged and reversible."*

## If something goes wrong

| Problem | Do this |
|---|---|
| A plan takes ~20 s | You started without `MAESTRO_LLM=none`. Wait, or restart with it. |
| No sound | Check the volume; Voice → Spoken replies → Test voice. The demo works without sound. |
| Step 8 says the folder is missing | Re-run the "Before the demo" commands. |
| Undo is not offered | Undo is only offered for a completed run with reversible steps; run step 8–9 again. |
| Microphone does nothing (step 13) | Skip it: macOS gives microphone permission to the app that started MAESTRO. Test it beforehand from that same app. |

## After the demo

Remove the demo folder and, if you want a clean history, MAESTRO's local state:

```bash
rm -r ~/maestro_workspace/demo
```

(MAESTRO's history and audit log live in `~/.maestro`; keep them if you want the
demo recorded.)

## Verified

Steps 2–11 were run on the real app on 13 and 17 October 2026: the same prompts,
the same outcomes, a verified undo of all 3 files, and the audit log intact.
Step 13 has been tested with a simulated microphone only; rehearse it on the
demo machine first.
