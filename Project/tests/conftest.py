"""Shared fixtures.

Every test that touches the filesystem runs inside a temporary workspace with a
temporary path policy. No test may read or write the real ~/Downloads — a test
suite for a file-automation agent that operates on the developer's actual home
directory is a test suite that will eventually delete something.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import maestro.executor  # noqa: F401  (registers verbs + executors)
from maestro.executor.base import Context
from maestro.safety import AuditLog, ConsentGate, PathPolicy
from maestro.safety.consent import Approval


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point MAESTRO_HOME / MAESTRO_WORKSPACE into tmp for every test."""
    home = tmp_path / "state"
    workspace = tmp_path / "workspace"
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MAESTRO_HOME", str(home))
    monkeypatch.setenv("MAESTRO_WORKSPACE", str(workspace))
    monkeypatch.setenv("MAESTRO_LLM", "none")
    yield


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def policy(tmp_path) -> PathPolicy:
    """Allows tmp_path only, minus any deny root that contains it.

    On Windows tmp lives under ~/AppData, which is denylisted; without this the
    whole suite would exercise nothing but the denylist.
    """
    from maestro.safety.paths import DEFAULT_DENY_DIRS

    root = tmp_path.resolve()

    def contains(deny: str) -> bool:
        try:
            root.relative_to(PathPolicy._canon(deny))
            return True
        except ValueError:
            return False

    return PathPolicy(allow_roots=[str(tmp_path)],
                      deny_dirs=[d for d in DEFAULT_DENY_DIRS if not contains(d)])


@pytest.fixture
def ctx() -> Context:
    return Context()


@pytest.fixture
def audit(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "audit.db")


@pytest.fixture
def approve_gate() -> ConsentGate:
    return ConsentGate(ask=lambda req: Approval(
        True, "typed" if req.gate == "typed_confirm" else "click"))


@pytest.fixture
def deny_gate() -> ConsentGate:
    return ConsentGate(ask=lambda req: Approval(False, "denied", note="test denial"))


@pytest.fixture
def files(workspace):
    """Factory: files(dir, "a.pdf", "b.pdf") -> [Path, ...]."""

    def make(subdir: str, *names: str, content: str = "x" * 64) -> list[Path]:
        d = workspace / subdir
        d.mkdir(parents=True, exist_ok=True)
        out = []
        for n in names:
            p = d / n
            p.write_text(content, encoding="utf-8")
            out.append(p)
        return out

    return make


def is_windows() -> bool:
    return os.name == "nt"
