"""L5 — the NLP layer (docs/05-NLP-AND-TRAINING.md §1).

    intents     the 16-class taxonomy + required slots per intent
    classifier  stage 1: rule baseline and trained TF-IDF/LinearSVC
    entities    stage 2: regex + gazetteer slot extraction
    clarify     FR-06: ask instead of guessing

Three stages rather than one prompt, because each stage is then separately
measurable and stage 1 gives a calibrated confidence to gate on.
"""

from maestro.nlp.clarify import Clarification, ClarificationManager
from maestro.nlp.classifier import (
    RuleIntentClassifier,
    SklearnIntentClassifier,
    load_classifier,
    needs_clarification,
    safety_prefilter,
)
from maestro.nlp.entities import Entity, EntityExtractor, Slots, resolve_path
from maestro.nlp.intents import (
    CONFIDENCE_THRESHOLD,
    INTENT_VERBS,
    INTENTS,
    REFUSAL_INTENTS,
    REQUIRED_SLOTS,
    IntentPrediction,
)

__all__ = [
    "RuleIntentClassifier",
    "SklearnIntentClassifier",
    "load_classifier",
    "needs_clarification",
    "safety_prefilter",
    "Clarification",
    "ClarificationManager",
    "Entity",
    "EntityExtractor",
    "Slots",
    "resolve_path",
    "CONFIDENCE_THRESHOLD",
    "INTENTS",
    "INTENT_VERBS",
    "REFUSAL_INTENTS",
    "REQUIRED_SLOTS",
    "IntentPrediction",
]
