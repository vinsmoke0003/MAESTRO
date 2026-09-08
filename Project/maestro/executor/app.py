"""Application-control verbs (PRD task category T4).

    app.launch  R1  start an application            (reversible: quit it)
    app.quit    R1  ask an application to quit      (reversible: relaunch it)

The verb, its args schema, its risk tier and its dry run are written once here.
Only the two lines that actually talk to the OS are delegated to
`maestro/executor/{win32,darwin}/apps.py`. That ratio — one shared file, a
handful of per-platform lines — is the cross-platform argument from docs/02 §7
made concrete.

`app_id` is a *name from a gazetteer*, never a path and never a command line.
There is no shell anywhere in the call chain, so an application name containing
`; rm -rf ~` is a name that will not be found, not a command.
"""

from __future__ import annotations

from pydantic import BaseModel

from maestro.executor.base import (
    Context,
    EffectManifest,
    NotAvailable,
    Result,
    register_executor,
    resolve,
)
from maestro.executor.platform import backend
from maestro.ir import Risk
from maestro.registry import VerbSpec, register


class LaunchArgs(BaseModel):
    app_id: str


class QuitArgs(BaseModel):
    app_id: str
    force: bool = False


register(VerbSpec("app.launch", LaunchArgs, Risk.R1, reversible=True, category="app",
                  description="Launch an application by name"))
register(VerbSpec("app.quit", QuitArgs, Risk.R1, reversible=True, category="app",
                  description="Quit an application by name"))


class LaunchExecutor:
    verb = "app.launch"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = LaunchArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Launch {a.app_id}",
            unknowns=["a launched application may open windows or documents of its own"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = LaunchArgs.model_validate(resolve(args, ctx))
        try:
            detail = backend("apps").launch(a.app_id)
        except NotAvailable as e:
            return Result(ok=False, detail=str(e))
        return Result(ok=True, output=a.app_id, detail=detail,
                      undo_data={"launched": a.app_id})

    def undo(self, result: Result, ctx: Context) -> None:
        d = result.undo_data or {}
        if d.get("launched"):
            try:
                backend("apps").quit(d["launched"], force=False)
            except NotAvailable:
                pass


class QuitExecutor:
    verb = "app.quit"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = QuitArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Quit {a.app_id}",
            unknowns=["unsaved work in the application may be lost"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = QuitArgs.model_validate(resolve(args, ctx))
        try:
            detail = backend("apps").quit(a.app_id, force=a.force)
        except NotAvailable as e:
            return Result(ok=False, detail=str(e))
        return Result(ok=True, output=a.app_id, detail=detail,
                      undo_data={"quit": a.app_id})

    def undo(self, result: Result, ctx: Context) -> None:
        d = result.undo_data or {}
        if d.get("quit"):
            try:
                backend("apps").launch(d["quit"])
            except NotAvailable:
                pass


for _ex in (LaunchExecutor(), QuitExecutor()):
    register_executor(_ex)
