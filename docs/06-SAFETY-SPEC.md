# 06 — Safety Layer Specification

**Project MAESTRO** · v1.0

> **This document is the project's research contribution.** Everything else — the planner, the executors, the voice interface — exists so that this layer has something to govern. If you read only one document twice, read this one.

---

## 1. Threat Model

Name your adversaries explicitly. A safety claim without a threat model is marketing.

| # | Threat | Example | Primary control |
|---|---|---|---|
| **T1** | **Misinterpretation** — model correctly follows a wrong reading of an ambiguous instruction | "Clean up Downloads" → deletes everything | Dry-run preview + consent gate |
| **T2** | **Over-reach** — plan does more than asked | "Move PDFs" → also reorganizes other folders | Plan diff vs. intent; Critic agent; consent |
| **T3** | **Indirect prompt injection** — untrusted content issues instructions | Malicious text inside a PDF or webpage | Trust tagging; Summarizer has no tools; fixed DAG |
| **T4** | **Irreversible action** — correct action, unrecoverable outcome | Permanent delete; email sent | Trash-not-delete; hard-block; typed confirmation |
| **T5** | **Scope escape** — access outside declared workspace | Reading `~/.ssh`, writing to `/System` | Path allowlist + denylist |
| **T6** | **Capability escalation** — invoking something outside the intended verb set | Emitting a shell-exec action | Closed verb registry |
| **T7** | **Resource exhaustion** — runaway plan | Infinite loop; 100k file operations | Budget guard |
| **T8** | **Silent failure** — reports success without achieving the goal | Move "succeeded", files still in place | Postcondition verification |
| **T9** | **Repudiation** — no record of what happened | User cannot reconstruct events | Hash-chained audit log |

T3 and T8 are the two that most existing systems handle poorly, and they are where your evaluation will be most interesting.

---

## 2. Risk Taxonomy

Four tiers. Two orthogonal factors: **severity** of the effect and **reversibility** of it.

| Tier | Name | Definition | Policy | Examples |
|---|---|---|---|---|
| **R0** | SAFE | No state change outside MAESTRO's process. Pure reads. | Auto-execute, log only | `fs.glob`, `fs.read_text`, `sys.info`, `search.*`, `browser.extract` |
| **R1** | LOW | Reversible change confined to the declared workspace | Auto-execute, log, push undo | `fs.mkdir`, `fs.copy` (within workspace), `app.launch`, `sys.set_volume` |
| **R2** | MEDIUM | Reversible but consequential, **or** any write outside the workspace, **or** any external network write | **Explicit confirmation** + undo | `fs.move`, `fs.trash`, `browser.fill`, `browser.download`, `draft.email` |
| **R3** | HIGH | Irreversible, externally visible, or security-relevant | **Typed confirmation**; several verbs **hard-blocked outright** | `fs.delete_permanent`, `email.send`, credential entry, purchases, `sudo` |

### The scoring function is deterministic code

```python
def score_risk(action: Action, ctx: Context) -> Risk:
    if action.verb in HARD_BLOCKED:            return Risk.R3_BLOCKED
    base = VERB_REGISTRY[action.verb].base_risk

    for p in extract_paths(action.args):
        if in_denylist(p):                     return Risk.R3_BLOCKED
        if not in_allowlist(p):                base = max(base, Risk.R2)
        if is_system_path(p):                  return Risk.R3_BLOCKED

    if action.undo is None and base >= Risk.R1: base = max(base, Risk.R3)
    if affected_count(action, ctx) > BULK_N:    base = max(base, Risk.R2)
    if touches_external_network_write(action):  base = max(base, Risk.R2)
    if any_arg_derived_from_untrusted(action):  base = max(base, Risk.R2)
    return base
```

**Plan risk = max(action risks).** One R2 action makes the whole plan require consent — the user approves a plan, not a sequence of individually-approved steps.

Three properties to state and defend in the report:

1. **No LLM is called in this function.** Consequently the safety decision is reproducible, unit-testable, and immune to prompt manipulation. This is the design decision that makes the entire safety layer *evaluable*.
2. **Monotonic escalation.** Every rule can only raise risk, never lower it. A bug in one rule cannot silently downgrade a dangerous action.
3. **Fail-closed.** Unknown verb, unparseable path, unresolvable variable → treated as R3. When in doubt, the answer is "ask the human."

### Path policy

```python
ALLOWLIST = ["~/Desktop", "~/Documents", "~/Downloads", "~/Pictures", "~/maestro_workspace"]
DENYLIST  = ["~/.ssh", "~/.aws", "~/.config", "~/Library/Keychains", "~/.gnupg",
             "/System", "/Library", "/etc", "/var", "/usr", "C:\\Windows",
             "C:\\Program Files", "%APPDATA%", "*.key", "*.pem", "id_rsa*",
             ".env", "*.kdbx", "*wallet*"]
```

Denylist is checked first and wins. Paths are canonicalized (`realpath`) **before** matching — otherwise `~/Downloads/../../.ssh/id_rsa` walks straight through your allowlist. Symlinks are resolved and re-checked; a symlink in an allowed directory pointing at a denied one is a denied path. Write the test for that case in Week 5, because it is the bug you would otherwise ship.

---

## 3. The Dry-Run Simulator

Consent is meaningless if the user cannot see what they are approving. The dry run executes the entire plan against a **simulated filesystem/browser state** and produces an effect manifest, with zero real side effects.

```
MAESTRO will perform 3 actions:

  1. Find PDFs in ~/Downloads                                    [R0 safe]
     → 47 files, 312 MB

  2. Create ~/Documents/Invoices                                  [R1 low]
     → new directory ↩ undoable

  3. Move 47 files → ~/Documents/Invoices                      [R2 medium]
     → ⚠ 2 filename collisions: statement.pdf, receipt.pdf
       (will be renamed statement (1).pdf, receipt (1).pdf)
     → ↩ undoable · no files leave your machine

  Nothing outside ~/Downloads and ~/Documents is touched.
  Estimated 4 seconds.

  [ Approve ]   [ Approve & remember this pattern ]   [ Edit ]   [ Cancel ]
```

Requirements: every executor implements `dry_run()`; the simulator surfaces **collisions, overwrites, bulk counts, and total bytes**; anything the simulator cannot predict is stated as unknown rather than omitted. An honest "I can't predict this step's effect" is safe; a silently incomplete preview is not.

**Novelty note for the report:** dry-run-before-consent is standard in infrastructure tooling (`terraform plan`, `rsync --dry-run`) and almost entirely absent from LLM desktop agents. Borrowing a proven idea from an adjacent field and being the first to apply and *measure* it here is a perfectly respectable form of contribution — say so directly rather than overclaiming invention.

---

## 4. Consent Model

| Plan risk | Gate | Rationale |
|---|---|---|
| R0 | None; logged | Reads can't hurt. Prompting here trains users to click through — a real harm. |
| R1 | None; logged; undo offered | Reversible and in-workspace |
| R2 | **Click-to-approve** on the full preview | The default gate |
| R3 | **Typed confirmation** (retype a token, e.g. `DELETE 47 FILES`) | Deliberate friction where it is warranted |
| R3-blocked | **Refused.** Explained. No override path. | See §5 |

**Anti-habituation is a design requirement, not a nicety.** If a system prompts constantly, users approve reflexively and the gate provides no protection — the mechanism is defeated by its own frequency. Hence: R0/R1 never prompt, "approve & remember" allows a *specific* pattern to auto-approve within a session, and the **False Confirmation Rate** is a first-class metric you are trying to *minimize* ([Evaluation](07-EVALUATION.md)). Prompting on everything is not the safe choice; it is the choice that looks safe.

---

## 5. Hard Blocks

These are refused regardless of user instruction, user confirmation, or configuration. There is deliberately **no override flag**.

| Blocked | Why no override |
|---|---|
| Entering passwords, API keys, card numbers, government IDs | An agent that can type credentials can leak them; an override makes it a phishing target |
| Purchases or financial transfers | Irreversible, externally binding |
| Account creation | Binding to terms on the user's behalf |
| CAPTCHA solving | Circumvents an anti-automation control by design |
| Permanent delete (`fs.delete_permanent`) | Trash exists; there is no legitimate need |
| Sending email/messages autonomously | Irreversible and externally visible; drafting is offered instead |
| Modifying OS security settings, disabling protections | Escalation vector |
| `sudo` / Administrator elevation | Escapes every other control in this document |
| Arbitrary shell execution | Would nullify the closed verb registry |

Refusal is a **tested behavior with its own accuracy metric**, not an exception path. And the absence of an override flag is itself the design argument: a safety control with a bypass is a safety control that will be bypassed, usually by a user who has been prompted twelve times already that afternoon.

---

## 6. Injection Defense

The full trust model is in [Architecture §6](02-ARCHITECTURE.md#6-trust-model--injection-defense). The controls, in the order they fire:

| # | Control | Blocks |
|---|---|---|
| 1 | **Context isolation** — untrusted content never enters the Planner | The attack at its source |
| 2 | **Tool-less Summarizer** — the only component that reads untrusted content has no tools and cannot emit IR | Injected instructions have no way to become actions |
| 3 | **Fixed DAG** — the plan cannot grow after consent | Mid-execution escalation |
| 4 | **Closed verb registry** — no such verb as "exfiltrate" | Novel capability invention |
| 5 | **Path denylist** — `~/.ssh` unreachable by any plan | The most common payload target |
| 6 | **Hard blocks** — `email.send` unavailable to any plan | The most common exfiltration channel |
| 7 | **Taint tracking** — args derived from untrusted content escalate to R2 | Laundering data into sensitive parameters |
| 8 | **Audit log** — attempts recorded as `INJECTION_DETECTED` | Non-repudiation; also your evaluation data |

Report **which control fires first** for each of your 40 adversarial cases. A table showing that some attacks are stopped at layer 1 and others only at layer 5 demonstrates the layers are independently load-bearing — which is a much stronger claim than a single aggregate percentage, and it is the kind of analysis reviewers reward.

---

## 7. What We Do NOT Claim

**Put this section in the report, near the front, in your own words.** Stating limitations before an examiner finds them is the difference between a defence and a collapse — and it is also simply the honest thing to do.

MAESTRO is **not** "fully safe," and no LLM-driven agent with filesystem and browser access can be. Specifically:

- **No formal verification.** No proof of correctness or of safety. Our guarantees are empirical, measured on a benchmark we designed, and therefore bounded by that benchmark's coverage.
- **The allowlist is a policy, not a sandbox.** A bug in an executor could still touch a denied path. True isolation would require OS-level sandboxing (containers, seatbelt profiles, AppArmor) — named in Future Work.
- **Injection resistance is empirical.** We report resistance against 40 attacks we thought of. A novel attack may succeed. We report the rate, not immunity.
- **Consent depends on comprehension.** A user who approves without reading the preview is unprotected. We mitigate with anti-habituation design; we cannot eliminate it, and we measure it in the user study rather than assuming it away.
- **Undo is best-effort.** Reversing a file move is reliable. Reversing a browser form submission is not. The system labels which is which; it does not pretend the second case is recoverable.
- **The planner can still be wrong.** Safety controls limit the *blast radius* of a bad plan. They do not make plans correct.
- **A malicious user is out of scope.** The threat model assumes an authorized user and an untrusted environment — not an adversarial operator.

The honest summary, and the one to put in your abstract: *MAESTRO does not make desktop automation safe. It makes it **auditable, reversible, consent-gated, and injection-resistant** — four properties we define operationally and measure.* That sentence is defensible under hostile questioning. "Fully safe" is not, and the difference will be worth marks.
