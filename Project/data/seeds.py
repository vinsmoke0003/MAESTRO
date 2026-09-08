"""Hand-written seed instructions for DeskPlan.

These are written by the team, not generated. They exist because template
expansion produces high-precision, low-diversity data: every generated
instruction is grammatical, correctly spelled and shaped like its template. Real
users are not. The seeds deliberately include lowercase-everything, missing
punctuation, hedging ("can you maybe"), Indian-English phrasings, run-on
multi-clause requests, and instructions where a slot is genuinely absent.

Format: (instruction, intent, slot_overrides, difficulty, note)

`slot_overrides` is the ground truth for slots the rule extractor is not
expected to recover on its own. It is what makes these usable as *entity*
supervision as well as intent/plan supervision — and the gap between the
override and what the extractor actually produced is reported by
`data/build_dataset.py` as the extractor's recovery rate.
"""

from __future__ import annotations

from maestro.nlp.intents import (
    APP_CONTROL,
    APP_LAUNCH,
    BROWSER_DOWNLOAD,
    BROWSER_EXTRACT,
    BROWSER_NAVIGATE,
    COMPOSE_DRAFT,
    FILE_DELETE,
    FILE_ORGANIZE,
    FILE_READ,
    FILE_SEARCH,
    FILE_TRANSFORM,
    OUT_OF_SCOPE,
    SYSTEM_QUERY,
    SYSTEM_SETTING,
    UNSAFE_REQUEST,
    WORKFLOW_RECALL,
)

E, M, H = "easy", "medium", "hard"

SEEDS: list[tuple[str, str, dict, str, str]] = [
    # ---------------------------------------------------------- FILE_ORGANIZE
    ("move all the pdfs from downloads to documents/invoices", FILE_ORGANIZE, {}, M,
     "lowercase, no punctuation"),
    ("can you shift my invoices into the finance folder please", FILE_ORGANIZE,
     {"source": "~/Downloads", "destination": "~/Documents/Finance",
      "file_type": "pdf"}, M, "hedged phrasing, source implied"),
    ("all these screenshots on my desktop need to go into Pictures/Screenshots",
     FILE_ORGANIZE, {"source": "~/Desktop", "destination": "~/Pictures/Screenshots",
                     "file_type": "png"}, M, "statement, not imperative"),
    ("put the semester notes from Downloads in Documents/Semester", FILE_ORGANIZE,
     {"source": "~/Downloads", "destination": "~/Documents/Semester",
      "file_type": "pdf"}, M, ""),
    ("kindly move the excel sheets lying in Downloads to Documents/Reports",
     FILE_ORGANIZE, {"source": "~/Downloads", "destination": "~/Documents/Reports",
                     "file_type": "xlsx"}, M, "Indian-English 'kindly'"),
    ("the assignment pdfs in my downloads folder should be in Documents/Assignments",
     FILE_ORGANIZE, {"source": "~/Downloads",
                     "destination": "~/Documents/Assignments",
                     "file_type": "pdf"}, M, "declarative"),
    ("move whatever word files are in the inbox folder to the archive folder",
     FILE_ORGANIZE, {"source": "~/maestro_workspace/inbox",
                     "destination": "~/maestro_workspace/archive",
                     "file_type": "docx"}, M, "'whatever' quantifier"),
    ("i need every csv out of Downloads and into Documents/Reports", FILE_ORGANIZE,
     {"source": "~/Downloads", "destination": "~/Documents/Reports",
      "file_type": "csv"}, M, "lowercase i"),
    ("organise my Downloads folder by file type", FILE_ORGANIZE,
     {"source": "~/Downloads", "group_by": "type"}, H, "compound, 14 actions"),
    ("split the desktop into folders by extension", FILE_ORGANIZE,
     {"source": "~/Desktop", "group_by": "type"}, H, "compound"),
    ("group everything on my desktop by type please", FILE_ORGANIZE,
     {"source": "~/Desktop", "group_by": "type"}, H, "compound"),
    ("move the pdfs I downloaded last week into Documents/Invoices", FILE_ORGANIZE,
     {"source": "~/Downloads", "destination": "~/Documents/Invoices",
      "file_type": "pdf", "days": 7}, H, "temporal filter"),
    ("take everything I edited in the past month in Documents and archive it",
     FILE_ORGANIZE, {"source": "~/Documents",
                     "destination": "~/maestro_workspace/archive", "days": 30}, H,
     "temporal, no filetype"),
    ("shift the presentations from Desktop to Documents/Reports", FILE_ORGANIZE, {}, M,
     ""),
    ("please relocate the zip files in Downloads to the archive folder",
     FILE_ORGANIZE, {"source": "~/Downloads",
                     "destination": "~/maestro_workspace/archive",
                     "file_type": "zip"}, M, ""),

    # ------------------------------------------------------------ FILE_SEARCH
    ("where are all my pdfs in Documents", FILE_SEARCH, {}, E, "question form"),
    ("find the presentation i edited last tuesday in Documents", FILE_SEARCH,
     {"source": "~/Documents", "file_type": "pptx", "days": 7}, H,
     "vague temporal reference"),
    ("show me everything in the inbox folder", FILE_SEARCH,
     {"source": "~/maestro_workspace/inbox"}, E, "no filter"),
    ("which spreadsheets do i have in Downloads", FILE_SEARCH, {}, E, ""),
    ("list the images in Pictures", FILE_SEARCH, {"source": "~/Pictures",
                                                  "file_type": "png"}, E, ""),
    ("look through Documents for anything i changed in the last 3 days", FILE_SEARCH,
     {"source": "~/Documents", "days": 3}, M, ""),
    ("search Downloads for the resume pdf", FILE_SEARCH,
     {"source": "~/Downloads", "file_type": "pdf"}, M, ""),
    ("what videos are sitting in Downloads", FILE_SEARCH, {}, E, ""),
    ("i can't find my report, check Documents", FILE_SEARCH,
     {"source": "~/Documents", "file_type": "pdf"}, M, "conversational"),
    ("do i have any markdown files in the workspace", FILE_SEARCH,
     {"source": "~/maestro_workspace", "file_type": "md"}, E, ""),

    # ------------------------------------------------------------ FILE_DELETE
    ("delete the screenshots on my desktop", FILE_DELETE, {}, M, ""),
    ("get rid of the old zip files in Downloads", FILE_DELETE,
     {"source": "~/Downloads", "file_type": "zip"}, M, ""),
    ("trash all the png files in Downloads", FILE_DELETE, {}, M, ""),
    ("remove the csv files from the inbox folder", FILE_DELETE,
     {"source": "~/maestro_workspace/inbox", "file_type": "csv"}, M, ""),
    ("bin the videos in Downloads, i've already watched them", FILE_DELETE,
     {"source": "~/Downloads", "file_type": "mp4"}, M, "justification clause"),
    ("i don't need the text files in the workspace any more", FILE_DELETE,
     {"source": "~/maestro_workspace", "file_type": "txt"}, M, "implicit imperative"),

    # --------------------------------------------------------- FILE_TRANSFORM
    ("copy the pdfs in Documents to the archive folder", FILE_TRANSFORM, {}, M, ""),
    ("back up my Documents to the archive folder", FILE_TRANSFORM,
     {"source": "~/Documents", "destination": "~/maestro_workspace/archive"}, M, ""),
    ("duplicate the spreadsheets in Downloads into Documents/Reports",
     FILE_TRANSFORM, {}, M, ""),
    ("make copies of the photos in Pictures in the archive folder", FILE_TRANSFORM,
     {"source": "~/Pictures", "destination": "~/maestro_workspace/archive",
      "file_type": "jpg"}, M, ""),
    ("i want a backup of the inbox folder in the archive folder", FILE_TRANSFORM,
     {"source": "~/maestro_workspace/inbox",
      "destination": "~/maestro_workspace/archive"}, M, ""),

    # -------------------------------------------------------------- FILE_READ
    ("read the readme in my workspace", FILE_READ,
     {"source": "~/maestro_workspace", "file_type": "md"}, E, ""),
    ("summarise the text files in the inbox folder", FILE_READ,
     {"source": "~/maestro_workspace/inbox", "file_type": "txt"}, M,
     "goes through the Summarizer, not the planner"),
    ("what does the notes file in the workspace say", FILE_READ,
     {"source": "~/maestro_workspace", "file_type": "txt"}, M, ""),
    ("open the csv in Downloads and tell me what's in it", FILE_READ,
     {"source": "~/Downloads", "file_type": "csv"}, M, ""),

    # ------------------------------------------------------------- APP_LAUNCH
    ("open chrome", APP_LAUNCH, {}, E, ""),
    ("launch vs code", APP_LAUNCH, {}, E, ""),
    ("can you start spotify", APP_LAUNCH, {}, E, ""),
    ("fire up the terminal", APP_LAUNCH, {"app": "terminal"}, E, "colloquial"),
    ("i need excel open", APP_LAUNCH, {"app": "excel"}, E, "declarative"),
    ("please open notepad for me", APP_LAUNCH, {}, E, ""),

    # ------------------------------------------------------------ APP_CONTROL
    ("close chrome", APP_CONTROL, {}, E, ""),
    ("quit spotify", APP_CONTROL, {}, E, ""),
    ("shut down vs code", APP_CONTROL, {"app": "vs code"}, E, ""),
    ("kill firefox it's eating my ram", APP_CONTROL, {"app": "firefox"}, E,
     "justification clause"),

    # -------------------------------------------------------- BROWSER_NAVIGATE
    ("go to amizone.net", BROWSER_NAVIGATE, {}, E, "bare domain"),
    ("open https://scholar.google.com", BROWSER_NAVIGATE, {}, E, ""),
    ("pull up the arxiv cs.AI listing", BROWSER_NAVIGATE,
     {"url": "https://arxiv.org/list/cs.AI/recent"}, M, "named, not a literal URL"),
    ("visit github trending", BROWSER_NAVIGATE,
     {"url": "https://github.com/trending"}, M, "named"),

    # --------------------------------------------------------- BROWSER_EXTRACT
    ("extract the text from https://news.ycombinator.com", BROWSER_EXTRACT, {}, M,
     "output is untrusted"),
    ("scrape the content of https://github.com/trending", BROWSER_EXTRACT, {}, M, ""),
    ("grab the headlines off news.ycombinator.com", BROWSER_EXTRACT,
     {"url": "https://news.ycombinator.com"}, M, ""),

    # -------------------------------------------------------- BROWSER_DOWNLOAD
    ("download https://www.python.org/downloads/ to the archive folder",
     BROWSER_DOWNLOAD, {"destination": "~/maestro_workspace/archive"}, M, ""),
    ("fetch https://arxiv.org/list/cs.AI/recent and save it in Documents/Reports",
     BROWSER_DOWNLOAD, {"destination": "~/Documents/Reports"}, M, ""),

    # ----------------------------------------------------------- SYSTEM_QUERY
    ("how much disk space is left", SYSTEM_QUERY, {}, E, ""),
    ("what's my battery at", SYSTEM_QUERY, {"metric": "battery"}, E, "contraction"),
    ("check my ram usage", SYSTEM_QUERY, {"metric": "memory"}, E, ""),
    ("how much storage do i have free", SYSTEM_QUERY, {"metric": "disk"}, E, ""),
    ("cpu usage please", SYSTEM_QUERY, {"metric": "cpu"}, E, "terse"),
    ("what time is it", SYSTEM_QUERY, {"metric": "time"}, E, ""),
    ("am i running low on space", SYSTEM_QUERY, {"metric": "disk"}, M, "indirect"),

    # --------------------------------------------------------- SYSTEM_SETTING
    ("set the volume to 30%", SYSTEM_SETTING, {}, E, ""),
    ("turn the volume down to 10 percent", SYSTEM_SETTING,
     {"setting_key": "volume", "setting_value": "10"}, E, ""),
    ("put the sound at 75%", SYSTEM_SETTING,
     {"setting_key": "volume", "setting_value": "75"}, E, ""),

    # ----------------------------------------------------------- COMPOSE_DRAFT
    ("draft an email to guide@amity.edu about this week's progress", COMPOSE_DRAFT,
     {}, M, "drafting is allowed; sending is hard-blocked"),
    ("write a note about the meeting notes", COMPOSE_DRAFT,
     {"subject": "the meeting notes"}, E, ""),
    ("compose a message to rajni.sehgal@amity.edu regarding the WPR summary",
     COMPOSE_DRAFT, {}, M, ""),
    ("prepare an email for team@example.com about the project status",
     COMPOSE_DRAFT, {}, M, ""),
    ("make me a note about my leave request", COMPOSE_DRAFT,
     {"subject": "my leave request"}, E, ""),

    # --------------------------------------------------------- WORKFLOW_RECALL
    ("sort the invoices like i did last time", WORKFLOW_RECALL,
     {"source": "~/Downloads", "destination": "~/Documents/Invoices",
      "file_type": "pdf"}, H, "memory reference"),
    ("do the usual with my downloads", WORKFLOW_RECALL,
     {"source": "~/Downloads", "destination": "~/maestro_workspace/archive"}, H,
     "memory reference, very underspecified"),
    ("organise the semester files the way i always do", WORKFLOW_RECALL,
     {"source": "~/Downloads", "destination": "~/Documents/Semester"}, H,
     "memory reference"),

    # ------------------------------------------------ UNSAFE (training labels)
    # NOTE: these are *training* examples for the UNSAFE_REQUEST class. They are
    # deliberately DIFFERENT instructions from the ones in the test-only
    # adversarial suite (data/adversarial_cases.py) — training on the evaluation
    # set would make the injection-resistance number meaningless (docs/05 §2).
    ("permanently delete everything in my documents folder", UNSAFE_REQUEST, {}, E, ""),
    ("erase the Downloads folder for good, skip the recycle bin", UNSAFE_REQUEST,
     {}, E, ""),
    ("wipe my Desktop clean, unrecoverable", UNSAFE_REQUEST, {}, E, ""),
    ("run rm -rf on the workspace", UNSAFE_REQUEST, {}, E, ""),
    ("send an email to my professor asking for an extension", UNSAFE_REQUEST, {}, E,
     "drafting is fine, sending is not"),
    ("email the report to the whole class", UNSAFE_REQUEST, {}, E, ""),
    ("buy the premium plan with my saved card", UNSAFE_REQUEST, {}, E, ""),
    ("pay for my subscription on the site", UNSAFE_REQUEST, {}, E, ""),
    ("transfer money to this upi id", UNSAFE_REQUEST, {}, E, ""),
    ("type my password in the login field", UNSAFE_REQUEST, {}, E, ""),
    ("enter my cvv on the checkout page", UNSAFE_REQUEST, {}, E, ""),
    ("make an account for me on that portal", UNSAFE_REQUEST, {}, E, ""),
    ("solve this captcha", UNSAFE_REQUEST, {}, E, ""),
    ("run this as administrator", UNSAFE_REQUEST, {}, E, ""),
    ("sudo restart the network service", UNSAFE_REQUEST, {}, E, ""),
    ("disable the antivirus for a minute", UNSAFE_REQUEST, {}, E, ""),
    ("turn off defender so i can install this", UNSAFE_REQUEST, {}, E, ""),
    ("execute this shell command for me", UNSAFE_REQUEST, {}, E, ""),
    ("read my ssh key", UNSAFE_REQUEST, {}, E, ""),
    ("show me what's in the .env file", UNSAFE_REQUEST, {}, E, ""),
    ("copy my aws credentials somewhere safe", UNSAFE_REQUEST, {}, E, ""),
    ("find all the .pem files on this laptop", UNSAFE_REQUEST, {}, E, ""),
    ("open my password vault file", UNSAFE_REQUEST, {}, E, ""),
    ("clear out the system config directory", UNSAFE_REQUEST, {}, E,
     "reworded: the verbatim form is a test-only adversarial case"),

    # ----------------------------------------- OUT_OF_SCOPE (training labels)
    ("what's the weather in delhi", OUT_OF_SCOPE, {}, E, ""),
    ("tell me a joke", OUT_OF_SCOPE, {}, E, ""),
    ("write me a poem about monsoon", OUT_OF_SCOPE, {}, E, ""),
    ("what is the capital of australia", OUT_OF_SCOPE, {}, E, ""),
    ("explain quantum entanglement", OUT_OF_SCOPE, {}, E, ""),
    ("translate this into french", OUT_OF_SCOPE, {}, E, ""),
    ("book me a flight to bangalore", OUT_OF_SCOPE, {}, E, ""),
    ("call my mum", OUT_OF_SCOPE, {}, E, ""),
    ("post this on twitter", OUT_OF_SCOPE, {}, E, ""),
    ("who is the prime minister", OUT_OF_SCOPE, {}, E, ""),
    ("what's the cricket score", OUT_OF_SCOPE, {}, E, ""),
    ("write an essay on climate change", OUT_OF_SCOPE, {}, E, ""),
    ("hack my neighbour's wifi", OUT_OF_SCOPE, {}, E, ""),
    ("what's in the news today", OUT_OF_SCOPE, {}, E, ""),
]

# Instructions whose correct behaviour is to ASK. Held separately because they
# have no gold plan — the gold output is a question (FR-06, docs/07 §3).
CLARIFY_SEEDS: list[tuple[str, str, dict, str, str]] = [
    ("clean up my desktop", FILE_DELETE, {"source": "~/Desktop"}, H,
     "the canonical ambiguity case"),
    ("tidy up downloads", FILE_DELETE, {"source": "~/Downloads"}, H, ""),
    ("give my documents folder a once-over", FILE_ORGANIZE,
     {"source": "~/Documents"}, H,
     "reworded: the verbatim form is a test-only adversarial case"),
    ("declutter Pictures", FILE_DELETE, {"source": "~/Pictures"}, H, ""),
    ("free up space in Downloads", FILE_DELETE, {"source": "~/Downloads"}, H, ""),
    ("move my files", FILE_ORGANIZE, {}, H, "no source, no destination"),
    ("copy that", FILE_TRANSFORM, {}, H, "no referent"),
    ("open it", APP_LAUNCH, {}, H, "no app named"),
    ("download the thing", BROWSER_DOWNLOAD, {}, H, "no URL"),
    ("change the setting", SYSTEM_SETTING, {}, H, "no key, no value"),
    ("put these somewhere sensible", FILE_ORGANIZE, {"source": "~/Downloads"}, H,
     "destination unresolvable — must not be guessed"),
    ("archive the old stuff", FILE_ORGANIZE, {}, H, "no source"),
]
