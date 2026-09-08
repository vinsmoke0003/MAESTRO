"""The Critic — over-reach review before the user ever sees the plan (threat T2).

docs/06 §1 lists over-reach as its own threat: the plan does *more* than the
instruction asked for. "Move the PDFs" that also tidies three other folders is
not unsafe by the risk scorer's rules — every individual action is a legal,
reversible, in-workspace move — and it is still wrong.

The Critic is deliberately deterministic. An LLM critic reviewing an LLM plan
shares the failure mode it is meant to catch, and it would make the review
non-reproducible, which destroys the A5 ablation as an experiment. So the checks
here are plain rules over (intent, slots, plan):

    C1  verb outside the intent's allowed set     -> over-reach
    C2  destructive verb the instruction never implied
    C3  action touching a path unrelated to any slot
    C4  more actions than the intent's template needs (bloat)
    C5  an irreversible action with no undo declared
    C6  a read of untrusted content feeding a write

Findings do not block execution on their own — they are surfaced in the preview
so the human sees "MAESTRO noticed this plan does more than you asked" before
approving. Blocking is the scorer's job; noticing is the Critic's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from maestro import registry
from maestro.ir import Plan, Trust
from maestro.nlp.entities import Slots
from maestro.nlp.intents import INTENT_VERBS
from maestro.safety import taint as taint_mod

DESTRUCTIVE_VERBS = frozenset({"fs.trash", "fs.delete_permanent", "fs.move_batch",
                               "fs.rename", "app.quit"})
WRITE_VERBS = frozenset({"fs.write_text", "fs.move_batch", "fs.copy_batch", "fs.copy",
                         "fs.trash", "fs.rename", "draft.email", "draft.note",
                         "browser.download", "browser.fill", "browser.click"})

# How many actions each intent's deterministic template needs. A plan much
# larger than this is doing something the user did not ask for.
EXPECTED_STEPS: dict[str, int] = {
    "FILE_ORGANIZE": 3, "FILE_SEARCH": 1, "FILE_DELETE": 2, "FILE_TRANSFORM": 3,
    "FILE_READ": 2, "APP_LAUNCH": 1, "APP_CONTROL": 1, "BROWSER_NAVIGATE": 1,
    "BROWSER_EXTRACT": 1, "BROWSER_DOWNLOAD": 2, "SYSTEM_QUERY": 1,
    "SYSTEM_SETTING": 1, "COMPOSE_DRAFT": 1, "WORKFLOW_RECALL": 3,
}
BLOAT_SLACK = 2


@dataclass(frozen=True)
class Finding:
    code: str  # C1..C6
    action_id: str | None
    message: str
    severity: str = "warn"  # warn | serious


@dataclass
class CriticReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def serious(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "serious"]

    def render(self) -> str:
        if self.clean:
            return "Critic: no over-reach detected."
        lines = ["Critic findings:"]
        for f in self.findings:
            where = f" [{f.action_id}]" if f.action_id else ""
            mark = "!!" if f.severity == "serious" else " -"
            lines.append(f"  {mark}{where} {f.message}")
        return "\n".join(lines)

    def as_dicts(self) -> list[dict]:
        return [{"code": f.code, "action_id": f.action_id, "message": f.message,
                 "severity": f.severity} for f in self.findings]


class Critic:
    def review(self, plan: Plan, intent: str | None = None,
               slots: Slots | None = None, instruction: str = "") -> CriticReport:
        findings: list[Finding] = []
        text = (instruction or plan.instruction).lower()

        allowed = set(INTENT_VERBS.get(intent or "", ())) if intent else None
        for a in plan.actions:
            # C1 — verb outside the intent's allowed set
            if allowed is not None and allowed and a.verb not in allowed:
                findings.append(Finding(
                    "C1", a.action_id,
                    f"{a.verb} is not part of a {intent} task — the plan may be doing "
                    f"more than was asked", "serious"))

            # C2 — destructive verb the instruction never implied
            if a.verb in DESTRUCTIVE_VERBS and not _implies_destructive(text, a.verb):
                findings.append(Finding(
                    "C2", a.action_id,
                    f"{a.verb} changes or removes files, but the instruction did not "
                    f"ask for that", "serious"))

            # C5 — irreversible with no declared undo
            spec = _spec(a.verb)
            if spec and not spec.reversible and a.undo is None:
                findings.append(Finding(
                    "C5", a.action_id,
                    f"{a.verb} cannot be undone and the plan declares no inverse"))

        # C3 — paths unrelated to any slot the user actually mentioned
        if slots is not None:
            mentioned = {_norm(p) for p in (slots.source, slots.destination) if p}
            if mentioned:
                for a in plan.actions:
                    for p in _plan_paths(a.verb, a.args):
                        n = _norm(p)
                        if not any(n.startswith(m) or m.startswith(n) for m in mentioned):
                            findings.append(Finding(
                                "C3", a.action_id,
                                f"touches {p}, which is not the folder you named"))
                            break

        # C4 — step bloat
        expected = EXPECTED_STEPS.get(intent or "")
        if expected is not None and len(plan.actions) > expected + BLOAT_SLACK:
            findings.append(Finding(
                "C4", None,
                f"{len(plan.actions)} steps for a task that normally needs ~{expected}"))

        # C6 — untrusted read feeding a write
        reports = taint_mod.analyze(plan)
        for a in plan.actions:
            r = reports.get(a.action_id)
            if r and r.trust is Trust.T2 and a.verb in WRITE_VERBS:
                findings.append(Finding(
                    "C6", a.action_id,
                    f"{a.verb} is fed by content read from a file or web page; that "
                    f"content is untrusted", "serious"))

        return CriticReport(findings)


def _spec(verb: str):
    try:
        return registry.get(verb)
    except registry.RegistryError:
        return None


def _plan_paths(verb: str, args: dict) -> list[str]:
    spec = _spec(verb)
    if not spec:
        return []
    out: list[str] = []
    for name in spec.path_args:
        v = args.get(name)
        if isinstance(v, str) and not v.startswith("$"):
            out.append(v)
        elif isinstance(v, list):
            out.extend(x for x in v if isinstance(x, str) and not x.startswith("$"))
    return out


def _norm(p: str) -> str:
    """Both sides of the C3 comparison must be in the *same* form.

    The slots hold what the user said (`~/maestro_workspace/inbox`); the plan
    holds what the planner emitted (an absolute path, possibly relocated by
    MAESTRO_WORKSPACE). Resolving both to absolute before comparing is what
    stops C3 firing on every correctly-planned action.
    """
    from maestro.nlp.entities import resolve_path

    resolved = resolve_path(p) or p
    return resolved.replace("\\", "/").rstrip("/").lower()


DESTRUCTIVE_WORDS = {
    "fs.trash": ("delete", "remove", "trash", "bin", "get rid", "clear", "clean",
                 "erase", "discard", "throw"),
    "fs.delete_permanent": ("delete", "remove", "erase", "wipe", "shred"),
    "fs.move_batch": ("move", "organi", "sort", "file", "shift", "relocate", "put",
                      "archive", "tidy", "arrange", "group", "transfer", "clean"),
    "fs.rename": ("rename", "call", "name"),
    "app.quit": ("quit", "close", "exit", "stop", "kill"),
}


def _implies_destructive(text: str, verb: str) -> bool:
    return any(w in text for w in DESTRUCTIVE_WORDS.get(verb, ()))
