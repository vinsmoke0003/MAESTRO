"""Windows system-metric backend.

`psutil` is optional: every metric it would provide has a stdlib fallback, so
`sys.info` works on a clean Python install. Volume control needs `pycaw`, and
raises `NotAvailable` without it rather than silently doing nothing (T8).
"""

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
    if metric == "memory":
        try:
            import psutil

            vm = psutil.virtual_memory()
            return {"total_gb": round(vm.total / 1e9, 2),
                    "available_gb": round(vm.available / 1e9, 2),
                    "percent_used": vm.percent}
        except ImportError:
            raise NotAvailable("memory metric needs psutil (`pip install psutil`)") from None
    if metric == "battery":
        try:
            import psutil

            b = psutil.sensors_battery()
        except ImportError:
            raise NotAvailable("battery metric needs psutil") from None
        if b is None:
            return {"present": False}
        return {"present": True, "percent": b.percent, "plugged_in": b.power_plugged}
    if metric == "os":
        return {"system": platform.system(), "release": platform.release(),
                "version": platform.version(), "machine": platform.machine()}
    if metric == "cpu":
        try:
            import psutil

            return {"logical_cores": psutil.cpu_count(),
                    "percent": psutil.cpu_percent(interval=0.2)}
        except ImportError:
            return {"logical_cores": os.cpu_count()}
    if metric == "time":
        return {"local": datetime.now().isoformat(timespec="seconds"),
                "epoch": int(time.time())}
    if metric == "volume":
        return {"level": get_volume()}
    if metric == "network":
        try:
            import psutil

            counters = psutil.net_io_counters()
            return {"bytes_sent": counters.bytes_sent, "bytes_recv": counters.bytes_recv}
        except ImportError:
            raise NotAvailable("network metric needs psutil") from None
    raise NotAvailable(f"unsupported metric {metric!r}")


def _endpoint():
    try:
        from ctypes import POINTER, cast  # noqa: PLC0415

        from comtypes import CLSCTX_ALL  # type: ignore
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
    except ImportError as e:
        raise NotAvailable(
            "volume control on Windows needs pycaw (`pip install pycaw comtypes`)"
        ) from e
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def get_volume() -> int:
    vol = _endpoint()
    return int(round(vol.GetMasterVolumeLevelScalar() * 100))


def set_volume(level: int) -> None:
    vol = _endpoint()
    vol.SetMasterVolumeLevelScalar(max(0, min(100, int(level))) / 100.0, None)
