"""Fallback system backend: stdlib-only metrics, no volume control."""

from __future__ import annotations

import os
import platform
import shutil
import time
from datetime import datetime
from pathlib import Path

from maestro.executor.base import NotAvailable


def read_metric(metric: str, path: str = "~") -> dict:
    target = Path(path).expanduser()
    if metric == "disk":
        usage = shutil.disk_usage(target if target.exists() else Path.home())
        return {
            "total_gb": round(usage.total / 1e9, 2),
            "used_gb": round(usage.used / 1e9, 2),
            "free_gb": round(usage.free / 1e9, 2),
            "percent_used": round(100 * usage.used / usage.total, 1),
        }
    if metric == "os":
        return {"system": platform.system(), "release": platform.release(),
                "machine": platform.machine()}
    if metric == "cpu":
        return {"logical_cores": os.cpu_count()}
    if metric == "time":
        return {"local": datetime.now().isoformat(timespec="seconds"),
                "epoch": int(time.time())}
    raise NotAvailable(f"metric {metric!r} is not available on this platform")


def get_volume() -> int:
    raise NotAvailable("volume control is not implemented on this platform")


def set_volume(level: int) -> None:
    raise NotAvailable("volume control is not implemented on this platform")
