"""Generate the LLM-authored portion of DeskPlan (docs/05 §2, 1,400 pairs).

    python data/generate_llm_pairs.py --n 200
    MAESTRO_LLM=openai MAESTRO_LLM_BASE=https://api.groq.com/openai/v1 \
      MAESTRO_MODEL=llama-3.3-70b-versatile MAESTRO_API_KEY=... \
      python data/generate_llm_pairs.py --n 400

This script produces **candidates**, not dataset rows. Every line it writes has
`verified_by: null`, and `data/verify_candidates.py` is the gate between here
and `data/generated/llm_pairs.jsonl`.

That separation is the whole point. From docs/05 §2:

> The verification rule is absolute: no LLM-generated pair enters the dataset
> unverified. An unverified synthetic dataset teaches your model the
> generator's mistakes, and the resulting paper is not credible.

Two machine filters run first, so the human reviewer spends their time on
judgement rather than on syntax:

  * the model is asked for *instructions only*, never for plans. Gold plans are
    built by the same deterministic template library as the rest of the dataset,
    so a model cannot inject a wrong plan — only a wrong or duplicate sentence.
  * a candidate whose intent/slots do not survive the extractor and the IR
    validator is auto-rejected and counted.

Log the rejection rate. "We discarded 23% of generated candidates" is a good
number to report, not an embarrassing one.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import maestro.executor  # noqa: F401,E402
from data.build_dataset import CONTEXT_DATE, Dropped, make_record  # noqa: E402
from maestro.llm import LLMError, router  # noqa: E402
from maestro.nlp import EntityExtractor  # noqa: E402
from maestro.nlp.intents import INTENTS, REFUSAL_INTENTS  # noqa: E402
from maestro.safety import PathPolicy  # noqa: E402

OUT = ROOT / "data" / "generated" / "llm_candidates.jsonl"

SYSTEM = """You write realistic natural-language instructions for a desktop \
automation assistant. You do NOT write plans, code, or JSON schemas — only the \
sentences a real person would type or say.

Write the way people actually write: lowercase, missing punctuation, hedging \
("can you maybe"), abbreviations, and occasionally a run-on clause. Vary the \
folder names, file types and phrasing. Do not number the lines or add commentary.

Respond with JSON: {"instructions": ["...", "..."]}"""

INTENT_BRIEF: dict[str, str] = {
    "FILE_ORGANIZE": "moving or filing files from one folder into another",
    "FILE_SEARCH": "finding or listing files somewhere",
    "FILE_DELETE": "getting rid of files (they go to the Recycle Bin)",
    "FILE_TRANSFORM": "copying or backing up files",
    "FILE_READ": "reading or summarising the contents of files",
    "APP_LAUNCH": "opening an application",
    "APP_CONTROL": "closing or quitting an application",
    "BROWSER_NAVIGATE": "opening a web page",
    "BROWSER_EXTRACT": "extracting text from a web page",
    "BROWSER_DOWNLOAD": "downloading a file from a URL into a folder",
    "SYSTEM_QUERY": "asking about disk space, memory, battery, CPU or the time",
    "SYSTEM_SETTING": "changing the system volume",
    "COMPOSE_DRAFT": "drafting an email or a note (never sending it)",
    "WORKFLOW_RECALL": "referring to a task the user has done before",
    "OUT_OF_SCOPE": "something a desktop automation tool should not handle at all "
                    "(weather, jokes, general knowledge, booking things)",
    "UNSAFE_REQUEST": "something that must be refused: permanent deletion, sending "
                      "email autonomously, entering credentials, purchases, "
                      "privilege escalation, disabling security, shell execution",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "instructions": {"type": "array", "items": {"type": "string"},
                         "minItems": 1, "maxItems": 25},
    },
    "required": ["instructions"],
}


def prompt_for(intent: str, n: int, avoid: list[str]) -> str:
    brief = INTENT_BRIEF.get(intent, intent)
    lines = [
        f"Write {n} different instructions about {brief}.",
        "Use these folders: Downloads, Desktop, Documents, Pictures, "
        "Documents/Invoices, Documents/Finance, Documents/Reports, the archive folder.",
        "Use these file types: pdf, docx, xlsx, pptx, png, jpg, csv, txt, zip, mp4.",
    ]
    if avoid:
        lines.append("Do NOT repeat any of these, or anything close to them:")
        lines.extend(f"- {a}" for a in avoid[-12:])
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="generate LLM instruction candidates")
    ap.add_argument("--n", type=int, default=200, help="candidates to request in total")
    ap.add_argument("--per-call", type=int, default=15)
    ap.add_argument("--intents", nargs="*", default=None,
                    help="restrict to these intents (default: all 16)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    backend = router.pick()
    if not backend.available:
        print("No LLM backend is reachable.")
        print(f"  {router.describe()}")
        print()
        print("Start a local model:   ollama serve && ollama pull "
              "qwen2.5:7b-instruct-q4_K_M")
        print("Or point at a free cloud tier (your key, your account):")
        print("  MAESTRO_LLM=openai MAESTRO_LLM_BASE=... MAESTRO_MODEL=... "
              "MAESTRO_API_KEY=...")
        print()
        print("The rest of the dataset does not need this step — "
              "`python data/build_dataset.py` already produced 3,000+ pairs.")
        return 2

    intents = args.intents or INTENTS
    per_intent = max(1, args.n // len(intents))
    extractor = EntityExtractor(today=date.fromisoformat(CONTEXT_DATE))
    policy = PathPolicy()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["instruction"].strip().lower())

    kept = rejected = 0
    reasons: dict[str, int] = {}
    with out_path.open("a", encoding="utf-8") as f:
        for intent in intents:
            produced: list[str] = []
            while len(produced) < per_intent:
                want = min(args.per_call, per_intent - len(produced))
                try:
                    raw = backend.client.chat(SYSTEM, prompt_for(intent, want, produced),
                                              schema=SCHEMA)
                except LLMError as e:
                    print(f"  {intent}: provider error, skipping ({e})")
                    break
                try:
                    items = json.loads(raw).get("instructions", [])
                except json.JSONDecodeError:
                    reasons["unparseable response"] = reasons.get(
                        "unparseable response", 0) + 1
                    break
                if not items:
                    break
                for text in items:
                    text = str(text).strip()
                    key = text.lower()
                    if not text or key in seen:
                        rejected += 1
                        reasons["duplicate"] = reasons.get("duplicate", 0) + 1
                        continue
                    seen.add(key)
                    produced.append(text)

                    behavior = "refuse" if intent in REFUSAL_INTENTS else None
                    try:
                        rec = make_record(
                            f"dp_llm_{kept:05d}", text, intent, source="llm_generated",
                            extractor=extractor, policy=policy,
                            note=f"generated by {backend.model}", behavior=behavior,
                        )
                    except Dropped as e:
                        rejected += 1
                        reasons[str(e)[:40]] = reasons.get(str(e)[:40], 0) + 1
                        continue
                    rec["verified_by"] = None  # HUMAN REVIEW REQUIRED
                    rec["generator"] = backend.model
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    kept += 1
            print(f"  {intent:18s} kept {len(produced)}")

    total = kept + rejected
    print()
    print(f"{kept} candidate(s) written to {out_path}")
    print(f"{rejected}/{total} rejected "
          f"({100 * rejected / total:.0f}%) — report this number, it is a quality signal")
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:8]:
        print(f"    {n:4d}  {reason}")
    print()
    print("NEXT: python data/verify_candidates.py")
    print("Nothing here enters the dataset until a human sets verified_by.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
