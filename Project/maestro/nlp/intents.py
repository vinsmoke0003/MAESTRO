"""The intent taxonomy (docs/05-NLP-AND-TRAINING.md §1) and its required slots.

16 classes. Two of them carry more weight than the rest:

* `OUT_OF_SCOPE` and `UNSAFE_REQUEST` make correct refusal a *trained behaviour
  with a measurable accuracy*, rather than an accident of prompt wording. Their
  per-class F1 is reported in bold in the results chapter.

`REQUIRED_SLOTS` is what FR-06 (ask when unsure) is built on: an intent whose
required slots are not filled by the entity extractor produces a clarifying
question instead of a guessed path. "Never guess a path" is the rule — a wrong
path on an `fs.move_batch` is precisely the failure this project exists to
prevent, and it would be embarrassing for it to originate in our own resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FILE_ORGANIZE = "FILE_ORGANIZE"
FILE_SEARCH = "FILE_SEARCH"
FILE_DELETE = "FILE_DELETE"
FILE_TRANSFORM = "FILE_TRANSFORM"
FILE_READ = "FILE_READ"
APP_LAUNCH = "APP_LAUNCH"
APP_CONTROL = "APP_CONTROL"
BROWSER_NAVIGATE = "BROWSER_NAVIGATE"
BROWSER_EXTRACT = "BROWSER_EXTRACT"
BROWSER_DOWNLOAD = "BROWSER_DOWNLOAD"
SYSTEM_QUERY = "SYSTEM_QUERY"
SYSTEM_SETTING = "SYSTEM_SETTING"
COMPOSE_DRAFT = "COMPOSE_DRAFT"
WORKFLOW_RECALL = "WORKFLOW_RECALL"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
UNSAFE_REQUEST = "UNSAFE_REQUEST"

INTENTS: list[str] = [
    FILE_ORGANIZE,
    FILE_SEARCH,
    FILE_DELETE,
    FILE_TRANSFORM,
    FILE_READ,
    APP_LAUNCH,
    APP_CONTROL,
    BROWSER_NAVIGATE,
    BROWSER_EXTRACT,
    BROWSER_DOWNLOAD,
    SYSTEM_QUERY,
    SYSTEM_SETTING,
    COMPOSE_DRAFT,
    WORKFLOW_RECALL,
    OUT_OF_SCOPE,
    UNSAFE_REQUEST,
]

# The two classes whose F1 is reported in bold (docs/05 §1).
REFUSAL_INTENTS = frozenset({OUT_OF_SCOPE, UNSAFE_REQUEST})

# Slots that must be filled before an intent can be planned. Anything missing
# triggers a clarifying question (FR-06) rather than a guess.
REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    FILE_ORGANIZE: ("source", "destination"),
    FILE_SEARCH: ("source",),
    FILE_DELETE: ("source",),
    FILE_TRANSFORM: ("source",),
    FILE_READ: ("source",),
    APP_LAUNCH: ("app",),
    APP_CONTROL: ("app",),
    BROWSER_NAVIGATE: ("url",),
    BROWSER_EXTRACT: ("url",),
    BROWSER_DOWNLOAD: ("url",),
    SYSTEM_QUERY: ("metric",),
    SYSTEM_SETTING: ("setting_key", "setting_value"),
    COMPOSE_DRAFT: ("subject",),
    WORKFLOW_RECALL: (),
    OUT_OF_SCOPE: (),
    UNSAFE_REQUEST: (),
}

# Which verbs an intent is allowed to reach. Used by the rule planner and by
# the Critic's over-reach check (docs/06 §1 T2).
INTENT_VERBS: dict[str, tuple[str, ...]] = {
    FILE_ORGANIZE: ("fs.glob", "fs.mkdir", "fs.move_batch", "fs.copy", "fs.rename",
                    "search.by_name", "search.recent"),
    FILE_SEARCH: ("fs.glob", "fs.list_dir", "fs.stat", "search.by_name",
                  "search.by_content", "search.recent"),
    FILE_DELETE: ("fs.glob", "fs.trash", "search.by_name"),
    FILE_TRANSFORM: ("fs.glob", "fs.copy", "fs.copy_batch", "fs.rename", "fs.mkdir",
                     "fs.write_text"),
    FILE_READ: ("fs.glob", "fs.read_text", "fs.stat", "search.by_content"),
    APP_LAUNCH: ("app.launch",),
    APP_CONTROL: ("app.launch", "app.quit"),
    BROWSER_NAVIGATE: ("browser.open",),
    BROWSER_EXTRACT: ("browser.open", "browser.extract"),
    BROWSER_DOWNLOAD: ("browser.open", "browser.download", "fs.mkdir"),
    SYSTEM_QUERY: ("sys.info",),
    SYSTEM_SETTING: ("sys.set_volume", "sys.info"),
    COMPOSE_DRAFT: ("draft.email", "draft.note", "fs.glob", "fs.read_text"),
    WORKFLOW_RECALL: ("fs.glob", "fs.mkdir", "fs.move_batch", "fs.copy"),
    OUT_OF_SCOPE: (),
    UNSAFE_REQUEST: (),
}

# Expected behaviour per intent — the dataset's `expected_behavior` field
# (docs/05 §2) and the refusal-accuracy metric are both defined against this.
EXPECTED_BEHAVIOR: dict[str, str] = {
    OUT_OF_SCOPE: "refuse",
    UNSAFE_REQUEST: "refuse",
}


@dataclass(frozen=True)
class IntentPrediction:
    intent: str
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)
    source: str = ""  # model | rules

    @property
    def is_refusal(self) -> bool:
        """Class membership only — says nothing about how sure we are."""
        return self.intent in REFUSAL_INTENTS

    @property
    def is_confident_refusal(self) -> bool:
        """The property the pipeline actually acts on.

        Refusing needs positive evidence. `OUT_OF_SCOPE` is also where an
        unmatched instruction lands, so treating bare class membership as a
        refusal would turn "no rule fired" into "I won't do that" — which is
        both wrong and the most annoying possible failure mode. Below the
        threshold the instruction goes to the clarifier instead, and the user
        gets a question rather than a lecture.

        The deterministic prefilter in `classifier.py` returns 0.99, so genuine
        unsafe requests are unaffected by this.
        """
        return self.is_refusal and self.confidence >= CONFIDENCE_THRESHOLD

    def top(self, k: int = 3) -> list[tuple[str, float]]:
        return sorted(self.scores.items(), key=lambda kv: -kv[1])[:k]


# Confidence below this triggers a clarifying question (FR-06).
CONFIDENCE_THRESHOLD = 0.55
