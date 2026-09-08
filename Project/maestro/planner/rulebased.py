"""The deterministic plan builder.

It has two jobs, and the fact that it is *one* module doing both is deliberate:

1. **Baseline B0 / M0** in the evaluation (docs/07 §4, docs/05 §3). A rule
   planner is the floor that proves the task is not trivial — whatever it
   already solves, the LLM gets no credit for.
2. **The gold-plan generator for the DeskPlan dataset** (`data/build_dataset.py`).
   Gold plans and the rule baseline are produced by the same code, so the
   dataset can never contain a gold plan that the IR validator, the registry,
   or the scorer would reject. Every pair in `data/` is a plan that this system
   would actually accept.

It also makes the whole product work with no model installed: `maestro ask` on
a clean clone plans, previews, gates and executes using nothing but this file.
The LLM planner widens coverage; it is not load-bearing for any safety property.

Every template declares preconditions, postconditions and an undo where one
exists — the same obligations the LLM planner's output has to meet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from maestro.ir import Action, Check, Plan, PlannerInfo, Risk, UndoSpec
from maestro.nlp.entities import Slots, resolve_path
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
    SYSTEM_QUERY,
    SYSTEM_SETTING,
    WORKFLOW_RECALL,
)

# The extensions "organise by file type" partitions into. Deliberately a short,
# fixed list: the plan must be fully known before consent, and a per-type action
# for every extension on disk would blow the step budget and the preview.
GROUP_TYPES = ("pdf", "docx", "xlsx", "pptx", "png", "jpg", "zip")


class NoTemplate(Exception):
    """This intent/slot combination has no deterministic template.

    Raised, not papered over: a rule planner that improvises is no longer a
    baseline, and a gold plan that was guessed is not gold.
    """


@dataclass
class _B:
    """Tiny builder so the templates below read like the plans they produce."""

    actions: list[Action]

    def __init__(self) -> None:
        self.actions = []

    def add(self, verb: str, args: dict, *, produces: str | None = None,
            depends_on: list[str] | None = None, rationale: str = "",
            risk_hint: Risk | None = None, undo: UndoSpec | None = None,
            pre: list[Check] | None = None, post: list[Check] | None = None) -> str:
        aid = f"a{len(self.actions) + 1}"
        self.actions.append(Action(
            action_id=aid, verb=verb, args=args, produces=produces,
            depends_on=depends_on or [], rationale=rationale, risk_hint=risk_hint,
            undo=undo, preconditions=pre or [], postconditions=post or [],
        ))
        return aid


def _pattern(slots: Slots) -> str:
    if slots.file_name:
        return slots.file_name
    if slots.file_type:
        return f"*.{slots.file_type}"
    return "*"


def _abs(p: str | None) -> str | None:
    return resolve_path(p) if p else None


def build_actions(intent: str, slots: Slots, *, absolute: bool = True) -> list[Action]:
    """The templates. `absolute=False` keeps `~/...` for dataset gold plans."""
    conv = _abs if absolute else (lambda x: x)
    src = conv(slots.source)
    dst = conv(slots.destination)
    b = _B()

    # ---------------------------------------------------------------- files
    if intent in (FILE_ORGANIZE, WORKFLOW_RECALL) and slots.group_by == "type":
        # "organise Downloads by file type" — one glob + one move per extension,
        # so the dry run shows a real per-type count and each move is separately
        # undoable. A single opaque "organise" verb would show the user nothing.
        if not src:
            raise NoTemplate("organise-by-type needs a source folder")
        root = dst or src
        for ext in GROUP_TYPES:
            g = b.add("fs.glob", {"root": src, "pattern": f"*.{ext}", "recursive": False},
                      produces=f"{ext}_files", rationale=f"Find the .{ext} files",
                      risk_hint=Risk.R0,
                      post=[Check(check="var_defined", args={"var": f"{ext}_files"})])
            b.add("fs.move_batch", {"sources": f"${ext}_files",
                                    "dest_dir": f"{root}/{ext.upper()}"},
                  produces=f"{ext}_moved", depends_on=[g],
                  rationale=f"Move them into the {ext.upper()} subfolder",
                  risk_hint=Risk.R2,
                  undo=UndoSpec(verb="fs.restore_manifest",
                                args={"manifest": f"${ext}_moved"}),
                  post=[Check(check="var_defined", args={"var": f"{ext}_moved"})])
        return b.actions

    if intent in (FILE_ORGANIZE, WORKFLOW_RECALL):
        if not src or not dst:
            raise NoTemplate("FILE_ORGANIZE needs both a source and a destination")
        if slots.days:
            a1 = b.add("search.recent", {"root": src, "days": slots.days,
                                         "pattern": _pattern(slots)},
                       produces="matched", rationale=f"Find files changed in the last "
                                                     f"{slots.days} day(s)",
                       risk_hint=Risk.R0,
                       pre=[Check(check="path_exists", args={"path": src})],
                       post=[Check(check="var_defined", args={"var": "matched"})])
        else:
            a1 = b.add("fs.glob", {"root": src, "pattern": _pattern(slots),
                                   "recursive": slots.recursive},
                       produces="matched",
                       rationale=f"Find the {slots.file_type or 'matching'} files in {src}",
                       risk_hint=Risk.R0,
                       pre=[Check(check="path_exists", args={"path": src})],
                       post=[Check(check="var_defined", args={"var": "matched"})])
        a2 = b.add("fs.mkdir", {"path": dst},
                   rationale="Make sure the destination folder exists",
                   risk_hint=Risk.R1,
                   post=[Check(check="dir_exists", args={"path": dst})])
        b.add("fs.move_batch", {"sources": "$matched", "dest_dir": dst},
              produces="moved", depends_on=[a1, a2],
              rationale=f"Move them into {dst}", risk_hint=Risk.R2,
              undo=UndoSpec(verb="fs.restore_manifest", args={"manifest": "$moved"}),
              pre=[Check(check="dir_writable", args={"path": dst})],
              post=[Check(check="var_defined", args={"var": "moved"})])
        return b.actions

    if intent == FILE_TRANSFORM:
        if not src:
            raise NoTemplate("FILE_TRANSFORM needs a source")
        target = dst or f"{src}/backup"
        a1 = b.add("fs.glob", {"root": src, "pattern": _pattern(slots),
                               "recursive": slots.recursive},
                   produces="matched", rationale=f"Find the files to copy in {src}",
                   risk_hint=Risk.R0,
                   pre=[Check(check="path_exists", args={"path": src})],
                   post=[Check(check="var_nonempty", args={"var": "matched"})])
        a2 = b.add("fs.mkdir", {"path": target},
                   rationale="Make sure the destination folder exists", risk_hint=Risk.R1,
                   post=[Check(check="dir_exists", args={"path": target})])
        b.add("fs.copy_batch", {"sources": "$matched", "dest_dir": target},
              produces="copies", depends_on=[a1, a2],
              rationale=f"Copy them into {target}", risk_hint=Risk.R1,
              post=[Check(check="var_defined", args={"var": "copies"})])
        return b.actions

    if intent == FILE_SEARCH:
        if not src:
            raise NoTemplate("FILE_SEARCH needs a folder to search")
        if slots.days:
            b.add("search.recent", {"root": src, "days": slots.days,
                                    "pattern": _pattern(slots)},
                  produces="matches", rationale=f"List files changed in the last "
                                                f"{slots.days} day(s)", risk_hint=Risk.R0,
                  pre=[Check(check="path_exists", args={"path": src})],
                  post=[Check(check="var_defined", args={"var": "matches"})])
        elif slots.file_name or slots.file_type:
            b.add("fs.glob", {"root": src, "pattern": _pattern(slots),
                              "recursive": True},
                  produces="matches", rationale=f"Search {src} for matching files",
                  risk_hint=Risk.R0,
                  pre=[Check(check="path_exists", args={"path": src})],
                  post=[Check(check="var_defined", args={"var": "matches"})])
        else:
            b.add("fs.list_dir", {"path": src}, produces="matches",
                  rationale=f"List what is in {src}", risk_hint=Risk.R0,
                  pre=[Check(check="path_exists", args={"path": src})],
                  post=[Check(check="var_defined", args={"var": "matches"})])
        return b.actions

    if intent == FILE_DELETE:
        if not src:
            raise NoTemplate("FILE_DELETE needs a source")
        a1 = b.add("fs.glob", {"root": src, "pattern": _pattern(slots),
                               "recursive": slots.recursive},
                   produces="doomed", rationale=f"Find the files in {src}",
                   risk_hint=Risk.R0,
                   pre=[Check(check="path_exists", args={"path": src})],
                   post=[Check(check="var_defined", args={"var": "doomed"})])
        # Trash, never unlink (FR-26). The reversible verb is the only one that
        # exists for this intent; fs.delete_permanent is hard-blocked.
        b.add("fs.trash", {"paths": "$doomed"}, depends_on=[a1], produces="trashed",
              rationale="Move them to the Recycle Bin so they can be restored",
              risk_hint=Risk.R2,
              post=[Check(check="var_defined", args={"var": "trashed"})])
        return b.actions

    if intent == FILE_READ:
        if not src:
            raise NoTemplate("FILE_READ needs a file or folder")
        if slots.file_name:
            path = f"{src}/{slots.file_name}" if not src.endswith(slots.file_name) else src
            b.add("fs.read_text", {"path": path}, produces="content",
                  rationale=f"Read {slots.file_name}", risk_hint=Risk.R0,
                  pre=[Check(check="path_exists", args={"path": path})],
                  post=[Check(check="var_defined", args={"var": "content"})])
            return b.actions
        a1 = b.add("fs.glob", {"root": src, "pattern": _pattern(slots)},
                   produces="matched", rationale=f"Find the files to read in {src}",
                   risk_hint=Risk.R0,
                   pre=[Check(check="path_exists", args={"path": src})],
                   post=[Check(check="var_nonempty", args={"var": "matched"})])
        b.add("fs.stat", {"path": src}, depends_on=[a1], produces="info",
              rationale="Report what was found", risk_hint=Risk.R0,
              post=[Check(check="var_defined", args={"var": "info"})])
        return b.actions

    # ------------------------------------------------------------------ app
    if intent == APP_LAUNCH:
        if not slots.app:
            raise NoTemplate("APP_LAUNCH needs an application name")
        b.add("app.launch", {"app_id": slots.app}, rationale=f"Launch {slots.app}",
              risk_hint=Risk.R1, undo=UndoSpec(verb="app.quit",
                                               args={"app_id": slots.app}))
        return b.actions

    if intent == APP_CONTROL:
        if not slots.app:
            raise NoTemplate("APP_CONTROL needs an application name")
        b.add("app.quit", {"app_id": slots.app}, rationale=f"Quit {slots.app}",
              risk_hint=Risk.R1, undo=UndoSpec(verb="app.launch",
                                               args={"app_id": slots.app}))
        return b.actions

    # -------------------------------------------------------------- browser
    if intent == BROWSER_NAVIGATE:
        if not slots.url:
            raise NoTemplate("BROWSER_NAVIGATE needs a URL")
        b.add("browser.open", {"url": slots.url}, produces="page",
              rationale=f"Open {slots.url}", risk_hint=Risk.R1,
              post=[Check(check="var_defined", args={"var": "page"})])
        return b.actions

    if intent == BROWSER_EXTRACT:
        if not slots.url:
            raise NoTemplate("BROWSER_EXTRACT needs a URL")
        b.add("browser.extract", {"url": slots.url, "selector": "body"},
              produces="page_text", rationale=f"Extract the text of {slots.url}",
              risk_hint=Risk.R0,
              post=[Check(check="var_defined", args={"var": "page_text"})])
        return b.actions

    if intent == BROWSER_DOWNLOAD:
        if not slots.url:
            raise NoTemplate("BROWSER_DOWNLOAD needs a URL")
        target_dir = dst or conv("~/Downloads")
        name = slots.file_name or Path(slots.url.split("?")[0]).name or "download.bin"
        a1 = b.add("fs.mkdir", {"path": target_dir},
                   rationale="Make sure the download folder exists", risk_hint=Risk.R1,
                   post=[Check(check="dir_exists", args={"path": target_dir})])
        b.add("browser.download", {"url": slots.url, "dest": f"{target_dir}/{name}"},
              depends_on=[a1], produces="downloaded",
              rationale=f"Download {slots.url}", risk_hint=Risk.R2,
              undo=UndoSpec(verb="fs.trash", args={"paths": ["$downloaded"]}),
              post=[Check(check="var_defined", args={"var": "downloaded"})])
        return b.actions

    # --------------------------------------------------------------- system
    if intent == SYSTEM_QUERY:
        metric = slots.metric or "disk"
        b.add("sys.info", {"metric": metric, "path": src or "~"}, produces="metric_value",
              rationale=f"Read the {metric} metric", risk_hint=Risk.R0,
              post=[Check(check="var_defined", args={"var": "metric_value"})])
        return b.actions

    if intent == SYSTEM_SETTING:
        if slots.setting_key != "volume" or slots.setting_value is None:
            raise NoTemplate("the only settable system property in v1 is volume")
        b.add("sys.set_volume", {"level": int(slots.setting_value)},
              rationale=f"Set the volume to {slots.setting_value}%", risk_hint=Risk.R1)
        return b.actions

    # ---------------------------------------------------------------- draft
    if intent == COMPOSE_DRAFT:
        subject = slots.subject or "MAESTRO draft"
        if slots.recipients:
            b.add("draft.email", {"to": slots.recipients, "subject": subject,
                                  "body": _draft_body(subject, slots)},
                  produces="draft_path",
                  rationale="Write an UNSENT draft for you to review and send",
                  risk_hint=Risk.R2,
                  post=[Check(check="var_defined", args={"var": "draft_path"})])
        else:
            b.add("draft.note", {"title": subject, "body": _draft_body(subject, slots)},
                  produces="note_path", rationale="Write the note into your workspace",
                  risk_hint=Risk.R2,
                  post=[Check(check="var_defined", args={"var": "note_path"})])
        return b.actions

    raise NoTemplate(f"no deterministic template for intent {intent!r}")


def _draft_body(subject: str, slots: Slots) -> str:
    lines = [f"Draft prepared by MAESTRO about: {subject}.", ""]
    if slots.source:
        lines.append(f"Relevant folder: {slots.source}")
    lines.append("")
    lines.append("(This draft has NOT been sent. Review it and send it yourself.)")
    return "\n".join(lines)


def build_plan(instruction: str, intent: str, slots: Slots, *,
               absolute: bool = True, plan_id: str | None = None) -> Plan:
    actions = build_actions(intent, slots, absolute=absolute)
    return Plan(
        plan_id=plan_id or f"p_{uuid.uuid4().hex[:8]}",
        instruction=instruction,
        planner=PlannerInfo(model="rule-based", version="1.0.0", strategy="rule"),
        actions=actions,
    )


class RulePlanner:
    """Planner interface, so the pipeline can hold either this or the LLM one."""

    name = "rule"
    model = "rule-based"

    def plan(self, instruction: str, intent: str, slots: Slots) -> Plan:
        return build_plan(instruction, intent, slots)
