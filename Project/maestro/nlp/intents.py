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

# Google service intents. Deliberately NOT in INTENTS: the trained classifier
# and the DeskPlan dataset keep their 16 classes, so no reported number moves.
# These are reached only through the deterministic router in nlp/services.py,
# which runs after the safety prefilter and only when Gmail or Drive is named.
GMAIL_SEARCH = "GMAIL_SEARCH"
GMAIL_READ = "GMAIL_READ"
GMAIL_DRAFT = "GMAIL_DRAFT"
GMAIL_TO_CALENDAR = "GMAIL_TO_CALENDAR"   # reminders for tests/tickets found in mail
DRIVE_SEARCH = "DRIVE_SEARCH"
DRIVE_DOWNLOAD = "DRIVE_DOWNLOAD"
DRIVE_UPLOAD = "DRIVE_UPLOAD"
DRIVE_SHARE = "DRIVE_SHARE"      # planned so that the scorer can refuse it
DRIVE_DELETE = "DRIVE_DELETE"

SERVICE_INTENTS: list[str] = [GMAIL_SEARCH, GMAIL_READ, GMAIL_DRAFT, GMAIL_TO_CALENDAR,
                              DRIVE_SEARCH,
                              DRIVE_DOWNLOAD, DRIVE_UPLOAD, DRIVE_SHARE, DRIVE_DELETE]

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
    GMAIL_SEARCH: (),
    GMAIL_READ: (),
    GMAIL_DRAFT: ("recipients",),
    GMAIL_TO_CALENDAR: (),
    DRIVE_SEARCH: (),
    DRIVE_DOWNLOAD: ("query",),
    DRIVE_UPLOAD: ("source",),
    DRIVE_SHARE: (),
    DRIVE_DELETE: (),
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
    GMAIL_SEARCH: ("gmail.search",),
    GMAIL_READ: ("gmail.search", "gmail.read"),
    GMAIL_DRAFT: ("gmail.draft",),
    GMAIL_TO_CALENDAR: ("gmail.search", "gmail.read", "mail.find_events",
                        "calendar.add_events"),
    DRIVE_SEARCH: ("drive.search",),
    DRIVE_DOWNLOAD: ("drive.search", "drive.download"),
    DRIVE_UPLOAD: ("fs.glob", "drive.upload"),
    DRIVE_SHARE: ("drive.search", "drive.share"),
    DRIVE_DELETE: ("drive.search", "drive.delete"),
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
