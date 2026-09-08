"""System information and settings verbs (PRD task category T5).

    sys.info         R0  read a metric (disk, memory, battery, os, cpu, time)
    sys.set_volume   R1  set output volume 0-100   (reversible: restore prior)

`sys.info` is deliberately an *enum of metrics*, not a free-form query string.
A free-form query would be a shell in disguise, and the closed verb registry
would then be decorative (docs/06 §1 T6).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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

Metric = Literal["disk", "memory", "battery", "os", "cpu", "time", "volume", "network"]


class InfoArgs(BaseModel):
    metric: Metric = "disk"
    path: str = "~"


class VolumeArgs(BaseModel):
    level: int = Field(ge=0, le=100)


# `path` is NOT declared as a path_arg. It selects which *volume* to report on,
# and `shutil.disk_usage` reveals nothing about the directory itself — only the
# filesystem it sits on. Declaring it would escalate "how much disk space is
# left?" to R2 and prompt the user for a pure read, which is precisely the
# false-confirmation habituation failure docs/06 §4 warns about (and FCR
# measures). The executor still resolves the path defensively.
register(VerbSpec("sys.info", InfoArgs, Risk.R0, reversible=True, category="system",
                  description="Read a system metric: disk|memory|battery|os|cpu|time|"
                              "volume|network"))
register(VerbSpec("sys.set_volume", VolumeArgs, Risk.R1, reversible=True, category="system",
                  description="Set the output volume (0-100)"))


class InfoExecutor:
    verb = "sys.info"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = InfoArgs.model_validate(resolve(args, ctx))
        return EffectManifest(summary=f"Read system metric: {a.metric}")

    def execute(self, args: dict, ctx: Context) -> Result:
        a = InfoArgs.model_validate(resolve(args, ctx))
        try:
            value = backend("sysinfo").read_metric(a.metric, a.path)
        except NotAvailable as e:
            return Result(ok=False, detail=str(e))
        return Result(ok=True, output=value, detail=f"{a.metric}: {value}")

    def undo(self, result: Result, ctx: Context) -> None:
        pass


class VolumeExecutor:
    verb = "sys.set_volume"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = VolumeArgs.model_validate(resolve(args, ctx))
        return EffectManifest(summary=f"Set output volume to {a.level}%")

    def execute(self, args: dict, ctx: Context) -> Result:
        a = VolumeArgs.model_validate(resolve(args, ctx))
        mod = backend("sysinfo")
        try:
            previous = mod.get_volume()
            mod.set_volume(a.level)
        except NotAvailable as e:
            return Result(ok=False, detail=str(e))
        return Result(ok=True, output=a.level, detail=f"volume {previous} -> {a.level}",
                      undo_data={"previous": previous})

    def undo(self, result: Result, ctx: Context) -> None:
        d = result.undo_data or {}
        if d.get("previous") is not None:
            try:
                backend("sysinfo").set_volume(int(d["previous"]))
            except (NotAvailable, ValueError, TypeError):
                pass


for _ex in (InfoExecutor(), VolumeExecutor()):
    register_executor(_ex)
