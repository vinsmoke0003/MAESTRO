"""Stage 1 — intent classification (docs/05-NLP-AND-TRAINING.md §1).

Two implementations behind one interface:

* `RuleIntentClassifier` — keyword/priority rules. This is baseline B0/M0 in the
  results tables, and it is also the fallback when no trained artifact exists,
  so a fresh clone classifies intents without training anything first.
* `SklearnIntentClassifier` — TF-IDF (word 1-2 grams + char 3-5 grams) into a
  calibrated LinearSVC. Trained by `training/train_intent.py` on the DeskPlan
  dataset and persisted with joblib.

Why a classifier at all instead of one LLM call, restated from docs/05 §1: it is
separately measurable, it is ~3 orders of magnitude faster, and — the reason
that actually matters for safety — it produces a *calibrated confidence*, which
is what FR-06's "ask when unsure" threshold is defined against. A generative
model's assertion that it is confident is not a probability.

The unsafe/out-of-scope classes get a deterministic pre-filter that runs before
the model: refusal must not depend on whether a training set happened to cover a
phrasing. The model can add refusals; it can never remove one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from maestro.nlp.intents import (
    APP_CONTROL,
    APP_LAUNCH,
    BROWSER_DOWNLOAD,
    BROWSER_EXTRACT,
    BROWSER_NAVIGATE,
    COMPOSE_DRAFT,
    CONFIDENCE_THRESHOLD,
    FILE_DELETE,
    FILE_ORGANIZE,
    FILE_READ,
    FILE_SEARCH,
    FILE_TRANSFORM,
    INTENTS,
    OUT_OF_SCOPE,
    REFUSAL_INTENTS,
    SYSTEM_QUERY,
    SYSTEM_SETTING,
    UNSAFE_REQUEST,
    WORKFLOW_RECALL,
    IntentPrediction,
)

# --------------------------------------------------------------------------- #
# the deterministic safety pre-filter
# --------------------------------------------------------------------------- #

DESTRUCTIVE_VERB = (r"delete|remove|erase|wipe|purge|destroy|shred|clear|"
                    r"get\s+rid\s+of|obliterate|nuke")

UNSAFE_PATTERNS: list[tuple[str, str]] = [
    # "permanently" is the operative word wherever it appears in the sentence.
    # Matching only the adjacent pairs let "delete everything in Documents
    # permanently" through, which executed and destroyed the fixture — the one
    # unambiguous hard-block failure the first benchmark run turned up.
    (rf"\b(?:{DESTRUCTIVE_VERB})\b[^.?!]{{0,80}}\bpermanent(?:ly)?\b"
     rf"|\bpermanent(?:ly)?\b[^.?!]{{0,80}}\b(?:{DESTRUCTIVE_VERB})\b",
     "permanent-delete"),
    (r"\bshred\b|\bwipe\b|\bpurge\b|\bdestroy\b|\bobliterate\b|\bnuke\b|"
     r"\bblow\s+away\b|\btorch\b", "permanent-delete"),
    # Held-out run (eval/heldout.py, first run): "i dont need a bin" was read
    # as a clarification, not as an instruction to skip the Recycle Bin.
    (r"\b(don'?t|do\s+not|no)\s+(need|want)\s+(a|the)\s+(bin|trash|recycle)|"
     r"\bno\s+(bin|recycle\s+bin)\b|\bstraight\s+(to\s+)?(gone|deleted)\b",
     "permanent-delete"),
    (r"\bunrecoverabl\w*|\bbeyond\s+recovery|\bgone\s+for\s+good|\bfor\s+good\b|"
     r"\b(cannot|can'?t|nothing\s+can)\s+be\s+recovered|\bno\s+trash\b|"
     r"\bskip\s+the\s+(trash|recycle)|"
     r"\bwithout\s+(using\s+|sending\s+to\s+)?(the\s+)?(trash|recycle)|"
     r"\bdon'?t\s+use\s+the\s+(trash|recycle)|\bbypass\s+the\s+(trash|recycle)",
     "permanent-delete"),
    (r"\brm\s+-rf\b|\bformat\s+(the\s+)?[a-z]:|\bdel\s+/[sf]\b|\bdiskpart\b|"
     r"\bmkfs\b|\bfdisk\b", "destructive-shell"),
    (r"\bssh\s+key|\bid_rsa\b|\bid_ed25519\b|\bprivate\s+key\b|\.ssh\b|"
     r"\bkeychain\b|\bcredential\w*\b|\bpassword\s+(file|manager|vault)\b|"
     r"\bapi\s+key\b|\bsecret\s+key\b|\.pem\b|\.kdbx\b|\bdotenv\b|"
     r"\.env\b|\bpassword\s+vault\b|\bkeystore\b", "credential-access"),
    # "email X to Y" is a send. Exempted when the user asked for a *draft* —
    # see DRAFTING_RE below; the exemption is applied in code because a
    # variable-length lookbehind ("draft an email to ...") is not expressible
    # as a regex, and the version that tried it refused every drafting request.
    (r"\bsend\s+(an?\s+)?(e-?mail|message|text)\b|\bemail\s+it\s+to\b|"
     r"\bsend\s+it\s+to\s+[\w.+-]+@|"
     r"\bemail\b[^.?!]{0,60}\bto\s+[\w.+-]+@|"
     r"\be-?mail\s+(the|my|this|that|it|them)\b[^.?!]{0,60}\bto\b|"
     # "mail X to Y", "send X over to Y", "forward X to Y", "shoot Y an email".
     # Every one of these was a miss: the filter matched the word "email" but
     # not the half-dozen ordinary English ways of saying the same thing.
     r"\bmail\s+(the|my|these|those|it|them)\b[^.?!]{0,60}\bto\b|"
     r"\bsend\b[^.?!]{0,60}\b(over\s+)?to\s+[\w.+-]+@|"
     r"\b(forward|fwd)\b[^.?!]{0,60}\bto\s+[\w.+-]+@|"
     r"\b(shoot|fire|drop|ping)\s+[\w.+-]+@[\w.-]+\s+(an?\s+)?(e-?mail|message|note)|"
     r"\b(share|upload|post)\s+.{0,40}\bwith\s+[\w.+-]+@|"
     # "ping the whole class ... by email", "let everyone know via mail".
     r"\b(ping|notify|tell|inform|remind|message|let)\b[^.?!]{0,60}"
     r"\b(by|via|over|through)\s+(e-?)?mail\b|"
     r"\be-?mail\s+(the\s+)?(whole\s+|entire\s+)?(class|team|group|everyone|"
     r"everybody|all\s+of\s+them|the\s+list)\b", "autonomous-send"),
    (r"\bbuy\b|\bpurchase\b|\border\s+(me\s+)?(a|an|the)\b|\bpay\s+(the|my|for)\b|"
     r"\btransfer\s+(money|funds|the\s+balance|₹|\$|\d)|\bcheckout\b|"
     r"\bcheck\s+out\s+(my|the)\s+(cart|basket)|\brenew\s+my\s+subscription\b|"
     r"\bsend\s+(money|funds|₹|\$)", "financial-transaction"),
    (r"\bsudo\b|\bas\s+(an?\s+)?administrator\b|\brun\s+as\s+admin\b|"
     r"\belevate\s+(to\s+)?(privileg|admin)|\broot\s+access\b|"
     r"\bwith\s+admin(istrator)?\s+(rights|privileges)|"
     r"\bgive\s+(yourself|me|it|us)\s+admin\b|\badmin\s+(rights|access|privileges)\b|"
     r"\bmake\s+(yourself|me)\s+(an?\s+)?admin", "privilege-escalation"),
    (r"\b(disable|switch\s+off|turn\s+off|deactivate|kill|stop)\s+"
     r"(the\s+|real[\s-]?time\s+)?(firewall|antivirus|anti-virus|defender|"
     r"gatekeeper|sip|security|protection)"
     r"|\bswitch\s+off\s+real[\s-]?time\s+protection"
     # postposed particle: "switch the antivirus off", "turn defender off"
     r"|\b(switch|turn|shut)\s+(the\s+|my\s+)?(firewall|antivirus|anti-virus|"
     r"defender|gatekeeper|real[\s-]?time\s+protection|protection)\s+(off|down)\b",
     "security-downgrade"),
    (r"\bcaptcha\b|\bget\s+past\s+the\s+(captcha|bot\s+check)", "captcha-bypass"),
    (r"\b(create|make|register|set\s*up|open)\s+(an?\s+)?(new\s+)?(account|profile|"
     r"login)\b|\bsign\s+me\s+up\b", "account-creation"),
    # Credential entry: the object matters more than the verb. Enumerate the
    # secret types and accept any "put it in the form" verb.
    (r"\b(enter|type|fill\s*(in|out)?|key\s*in|input|paste|put)\b[^.?!]{0,30}"
     r"\b(password|passwd|pin|cvv|cvc|otp|one[\s-]?time\s+password|card\s+(details|"
     r"number)|credit\s+card|aadhaar|aadhar|passport\s+number|ssn|security\s+code)\b"
     r"|\bmy\s+(password|pin|cvv|otp)\b[^.?!]{0,30}\b(in|into|on)\s+the\s+"
     r"(form|page|field|box)"
     # "log me into amizone, the password is in my notes": authenticating on the
     # user's behalf is credential entry whatever verb wraps it.
     r"|\b(log|sign)\s+me\s+in(to)?\b|\blog\s*in\s+(to\s+\S+\s+)?(for|as)\s+me\b|"
     r"\bthe\s+password\s+is\b|\busing\s+my\s+(password|login|credentials)\b",
     "credential-entry"),
    (r"\bexecute\s+(this\s+|the\s+)?(shell|command|script)\b|\brun\s+this\s+command\b|"
     r"\bopen\s+(a|the)\s+(shell|terminal|command\s+prompt|powershell)\b"
     r"[^.?!]{0,40}\b(and\s+)?run\b"
     r"|\brun\s+the\s+(setup\s+)?script\b|\bexecute\s+the\s+\w+\s+script\b"
     r"|\bpipe\b[^.?!]{0,30}\b(into|to)\s+(ba)?sh\b|\b\|\s*(ba)?sh\b"
     r"|\bcurl\b[^.?!]{0,40}\|\s*\w+"
     # "run whatever is in setup.sh", "execute install.bat", "run that .ps1"
     r"|\b(run|execute|launch)\b[^.?!]{0,40}\.(sh|bat|ps1|cmd|exe|msi|vbs|py)\b"
     r"|\b(run|execute)\s+whatever\b", "arbitrary-execution"),
]
_UNSAFE = [(re.compile(p, re.I), label) for p, label in UNSAFE_PATTERNS]

OUT_OF_SCOPE_PATTERNS = [
    r"\bwhat('?s| is)\s+the\s+(weather|score|news)\b",
    r"\btell\s+me\s+a\s+joke\b",
    r"\bwho\s+(is|was|are)\b(?!.*\bfile\b)",
    r"\bwrite\s+(me\s+)?a\s+(poem|song|story|essay)\b",
    r"\btranslate\b.*\binto\s+\w+\b",
    r"\bwhat\s+is\s+the\s+capital\s+of\b",
    # Mental arithmetic is not a system metric. Without this the classifier keyed
    # on the leading "what is" and routed it to SYSTEM_QUERY.
    r"\bwhat('?s| is)\s+\d+\s*(times|plus|minus|multiplied|divided|[-+*/x])\s*\d",
    r"\bexplain\s+(quantum|relativity|photosynthesis)\b",
    r"\bbook\s+(me\s+)?(a\s+)?(flight|ticket|table|cab|uber)\b",
    r"\b(call|phone|ring)\s+(my|the)\b",
    r"\bpost\s+(this\s+)?(on|to)\s+(twitter|x|instagram|facebook|linkedin)\b",
    r"\bhack\b|\bcrack\b(?!\s+open)",
]
_OOS = [re.compile(p, re.I) for p in OUT_OF_SCOPE_PATTERNS]


# Asking for a DRAFT is not asking to send. `draft.email` exists so that the
# useful half of the capability stays available while the irreversible half
# does not exist as a verb at all (docs/06 §5) — so the prefilter must not
# collapse the two. The exemption is narrow: it suppresses only the
# `autonomous-send` label, and only when a drafting verb is actually present.
# "draft an email and send it" still contains "send an email" and is refused.
DRAFTING_RE = re.compile(
    r"\b(draft|compose|prepare)\b[^.?!]{0,40}\b(an?\s+)?(e-?mail|message|note)\b"
    r"|\bwrite\s+(me\s+)?(an?|the)\s+(e-?mail|message|note)\b"
    r"|\bemail\s+draft\b",
    re.I,
)
EXPLICIT_SEND_RE = re.compile(
    r"\b(send|deliver|fire|shoot)\s+(it|them|the\s+\w+|an?\s+\w+)?\s*(off|out)?\b"
    r"[^.?!]{0,20}\b(now|immediately|straight\s+away)?\b"
    r"|\band\s+send\b|\bthen\s+send\b|\bsend\s+it\b",
    re.I,
)


def safety_prefilter(text: str) -> tuple[str, str] | None:
    """Deterministic. Returns (intent, label) or None.

    Runs BEFORE the model and cannot be overridden by it. A refusal that
    depended on training coverage would not be a safety property.
    """
    drafting = bool(DRAFTING_RE.search(text)) and not EXPLICIT_SEND_RE.search(text)
    for rx, label in _UNSAFE:
        if rx.search(text):
            if label == "autonomous-send" and drafting:
                continue  # a draft request, not a send request
            return UNSAFE_REQUEST, label
    for rx in _OOS:
        if rx.search(text):
            return OUT_OF_SCOPE, "out-of-scope"
    return None


# --------------------------------------------------------------------------- #
# rule classifier (baseline + fallback)
# --------------------------------------------------------------------------- #

RULES: list[tuple[str, str, float]] = [
    # (intent, pattern, weight)
    (FILE_ORGANIZE, r"\b(move|organi[sz]e|sort|file away|tidy|arrange|relocate|shift|"
                    r"put|group|categori[sz]e|split|divide|separate|transfer)\b", 1.0),
    (FILE_ORGANIZE, r"\b(rename)\b", 0.4),
    (FILE_SEARCH, r"\b(find|search|look for|look through|locate|where (is|are)|"
                  r"where('s| are) my|show me|list|do i have|anything in)\b", 1.0),
    # "what csv files are in Downloads", "which presentations are in Documents".
    # A wh-question that names a container is a search. Anchoring on the
    # container word is what keeps "what time is it" out of this class.
    (FILE_SEARCH, r"\b(what|which)\b[^?]{0,60}\b(in|inside|under|folder|directory)\b",
     1.0),
    (FILE_DELETE, r"\b(delete|remove|trash|bin|get rid of|clear out|clean up|erase)\b", 1.0),
    # `zip` only counts as the verb "to zip". "delete the zip files" names a
    # file extension, and letting it score FILE_TRANSFORM ties the vote with
    # FILE_DELETE and produces a pointless clarifying question.
    (FILE_TRANSFORM, r"\b(copy|duplicate|backup|back up|rename|convert|compress)\b"
                     r"|\bzip\b(?!\s+(?:file|files)\b)", 1.0),
    # "contents of the archive folder" is a listing, not a read — the negative
    # lookahead is what stops it tying with FILE_SEARCH and forcing a pointless
    # clarifying question.
    (FILE_READ, r"\b(read|open the file|summari[sz]e|what does .* say|preview)\b"
                r"|\bcontents? of\b(?!.{0,30}\b(folder|directory)\b)", 1.0),
    (APP_LAUNCH, r"\b(open|launch|start|run)\b", 0.8),
    (APP_CONTROL, r"\b(close|quit|exit|kill|stop)\b", 1.0),
    (BROWSER_NAVIGATE, r"\b(go to|visit|navigate to|browse to|open .*(\.com|\.org|"
                       r"\.in|https?://))\b", 1.0),
    (BROWSER_EXTRACT, r"\b(scrape|extract|grab the|pull the|get the (text|headlines|"
                      r"prices|titles))\b", 1.0),
    (BROWSER_DOWNLOAD, r"\b(download|fetch|save .*(from|off) (the )?(web|site|url))\b", 1.0),
    # Keyed on the metric gazetteer rather than on a handful of phrasings.
    # "check my storage", "how is my cpu doing" and "what operating system am I
    # on" all failed the old pattern and fell through to OUT_OF_SCOPE.
    (SYSTEM_QUERY, r"\b(how much|how many|how('?s| is)|what|check|tell me|"
                   r"show me|am i (running )?low on)\b[^?]{0,40}"
                   r"\b(disk|storage|free space|space left|hard drive|memory|ram|"
                   r"battery|charge|cpu|processor|operating system|os version)\b", 1.4),
    # `time` needs its own rule: "like I did last time" is a memory reference,
    # not a clock query, and a bare \btime\b would score both.
    (SYSTEM_QUERY, r"\bwhat time is it\b|\bwhat('?s| is) (the|today'?s) (time|date)\b|"
                   r"\btell me the (time|date)\b|\bwhat day is it\b|"
                   r"\bwhat is the date today\b", 1.4),
    (SYSTEM_QUERY, r"\b(how much|how many|what('?s| is) my|status|space left|"
                   r"free space)\b", 1.0),
    (SYSTEM_SETTING, r"\b(set|turn|change|adjust|increase|decrease|mute|unmute)\b.*\b"
                     r"(volume|brightness|sound)\b", 1.2),
    (COMPOSE_DRAFT, r"\b(draft|compose|prepare (an?|me an?) (email|note|message)|"
                    r"(write|make|jot) (me )?(an?|down) ?(email|note|message|summary)|"
                    r"note down)\b", 1.4),
    (WORKFLOW_RECALL, r"\b(like (i did )?last time|the way i (usually|always)|same as "
                      r"(last|before)|as usual|my usual)\b", 1.5),
]
_RULES = [(intent, re.compile(p, re.I), w) for intent, p, w in RULES]


@dataclass
class RuleIntentClassifier:
    """Keyword baseline. Deterministic, ~20 microseconds, no training."""

    name: str = "rules"

    def predict(self, text: str) -> IntentPrediction:
        pre = safety_prefilter(text)
        if pre:
            return IntentPrediction(pre[0], 0.99, {pre[0]: 0.99}, "rules:prefilter")

        scores: dict[str, float] = dict.fromkeys(INTENTS, 0.0)
        for intent, rx, w in _RULES:
            if rx.search(text):
                scores[intent] += w

        # Disambiguation: "open chrome" is APP_LAUNCH, "open example.com" is
        # BROWSER_NAVIGATE, "open the report" is FILE_READ.
        if scores[APP_LAUNCH] and re.search(r"https?://|\.(com|org|net|in|io)\b", text, re.I):
            scores[BROWSER_NAVIGATE] += 1.0
            scores[APP_LAUNCH] -= 0.6
        # An explicit destructive verb outranks an incidental transform match:
        # "delete the backup copies" is a deletion, not a copy.
        if scores[FILE_DELETE] and scores[FILE_TRANSFORM] and re.search(
            r"\b(delete|remove|trash|bin|get rid of|erase)\b", text, re.I
        ):
            scores[FILE_TRANSFORM] -= 0.7

        # "organise/clean up X BY FILE TYPE" names a grouping, so it is an
        # organise, never a deletion. Without this, "clean up Downloads by file
        # type" scored FILE_DELETE — and the delete template, which ignores
        # group_by, would have planned glob("*") -> trash on the whole folder.
        # The no-target guard in clarify.py caught it, but the classifier is the
        # right place to fix it.
        from maestro.nlp.entities import GROUP_BY_TYPE_RE

        if GROUP_BY_TYPE_RE.search(text):
            scores[FILE_ORGANIZE] += 1.5
            scores[FILE_DELETE] = max(0.0, scores[FILE_DELETE] - 1.0)
        elif re.search(r"\b(clean up|tidy)\b", text, re.I):
            # Ambiguous-destructive with no grouping stated: the correct
            # behaviour is to clarify, and FILE_DELETE is the reading that
            # makes the clarification happen.
            scores[FILE_DELETE] += 0.3

        total = sum(max(0.0, v) for v in scores.values())
        if total <= 0:
            # Nothing matched. This is *uncertainty*, not a judgement that the
            # request is out of scope — and the difference matters, because
            # OUT_OF_SCOPE is a refusal class. The confidence is deliberately
            # below CONFIDENCE_THRESHOLD so `is_confident_refusal` stays False
            # and the clarifier asks a question instead of the pipeline
            # refusing. Only the deterministic prefilter refuses outright.
            return IntentPrediction(OUT_OF_SCOPE, 0.20, {OUT_OF_SCOPE: 0.20},
                                    "rules:no-match")
        probs = {k: max(0.0, v) / total for k, v in scores.items() if v > 0}
        best = max(probs, key=probs.get)  # type: ignore[arg-type]
        return IntentPrediction(best, probs[best], probs, "rules")


# --------------------------------------------------------------------------- #
# trained classifier
# --------------------------------------------------------------------------- #


class SklearnIntentClassifier:
    """TF-IDF -> calibrated LinearSVC. Loaded from a joblib artifact."""

    def __init__(self, pipeline, labels: list[str], meta: dict | None = None):
        self._pipe = pipeline
        self._labels = labels
        self.meta = meta or {}
        self.name = "sklearn"

    # -- construction ------------------------------------------------------

    @staticmethod
    def build():
        """The untrained pipeline. Kept here so training and inference cannot
        drift apart — a format mismatch between the two is the single most
        common cause of "the model got worse" (docs/05 §3)."""
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.pipeline import FeatureUnion, Pipeline
        from sklearn.svm import LinearSVC

        features = FeatureUnion([
            ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True,
                                     min_df=1, lowercase=True)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                     sublinear_tf=True, min_df=1, lowercase=True)),
        ])
        return Pipeline([
            ("features", features),
            ("clf", CalibratedClassifierCV(LinearSVC(C=1.0, class_weight="balanced"),
                                           cv=3, method="sigmoid")),
        ])

    @classmethod
    def load(cls, path: str | Path) -> SklearnIntentClassifier:
        import joblib

        blob = joblib.load(Path(path))
        return cls(blob["pipeline"], blob["labels"], blob.get("meta", {}))

    def save(self, path: str | Path) -> None:
        import joblib

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"pipeline": self._pipe, "labels": self._labels, "meta": self.meta}, p)

    # -- inference ---------------------------------------------------------

    def predict(self, text: str) -> IntentPrediction:
        pre = safety_prefilter(text)
        if pre:
            return IntentPrediction(pre[0], 0.99, {pre[0]: 0.99}, "sklearn:prefilter")

        probs = self._pipe.predict_proba([text])[0]
        scores = {label: float(p) for label, p in zip(self._pipe.classes_, probs)}
        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        confidence = scores[best]

        # The model may PROPOSE a refusal; only the deterministic prefilter may
        # DECIDE one — the same "model proposes, code decides" rule the risk
        # scorer lives by (docs/06 §2). If we reach this line the prefilter did
        # not fire, so a refusal-class prediction here is the model
        # generalising past its evidence: it refused "what operating system am
        # I on" at 0.78 in the benchmark. Capping the confidence below the
        # threshold turns that into a clarifying question, which is the right
        # outcome for an instruction the deterministic layer found nothing
        # wrong with. The prediction itself is kept so the classifier's own
        # accuracy is measured honestly — this is a gate on acting, not a
        # relabel.
        if best in REFUSAL_INTENTS:
            confidence = min(confidence, CONFIDENCE_THRESHOLD - 0.01)
        return IntentPrediction(best, confidence, scores, "sklearn")

    def predict_batch(self, texts: list[str]) -> list[IntentPrediction]:
        return [self.predict(t) for t in texts]


# --------------------------------------------------------------------------- #
# loader used by the pipeline
# --------------------------------------------------------------------------- #


def load_classifier(path: str | Path | None = None, *, quiet: bool = True):
    """Trained model if the artifact is present, rule baseline otherwise.

    Never raises: a missing or unloadable model degrades to rules, because an
    intent classifier failing to import must not take the desktop assistant
    down with it.
    """
    from maestro.config import settings

    p = Path(path) if path else settings().intent_model
    if p.exists():
        try:
            return SklearnIntentClassifier.load(p)
        except Exception as e:  # pragma: no cover - corrupt artifact
            if not quiet:
                print(f"[nlp] could not load {p}: {e}; falling back to rules")
    return RuleIntentClassifier()


def needs_clarification(pred: IntentPrediction,
                        threshold: float = CONFIDENCE_THRESHOLD) -> bool:
    """FR-06: below the threshold, ask instead of guessing."""
    return pred.confidence < threshold and not pred.is_refusal


def entropy(scores: dict[str, float]) -> float:
    """Reported alongside confidence in the calibration analysis."""
    vals = [v for v in scores.values() if v > 0]
    return -sum(v * math.log(v, 2) for v in vals) if vals else 0.0
