"""Learned preferences (FR-52) — "invoices go to Documents/Finance".

A preference is learned from *observed, consented* behaviour, never inferred
from a single ambiguous instruction. The rule implemented here:

    a (key, value) pair becomes a preference after it has been part of an
    approved, completed plan `MIN_OBSERVATIONS` times, and its confidence is
    observations / (observations + contradictions).

The entity extractor reads preferences as its `known_paths` gazetteer, so the
next time you say "put the invoices in the finance folder" the destination
resolves from memory instead of asking. That is the "learns you" behaviour,
and it is deliberately boring, inspectable code rather than a model — the user
can list it, edit it and delete it (FR-53), which they could not do to a weight.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MIN_OBSERVATIONS = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    key            TEXT PRIMARY KEY,
    value          TEXT NOT NULL,
    kind           TEXT DEFAULT 'path',
    observations   INTEGER DEFAULT 1,
    contradictions INTEGER DEFAULT 0,
    learned_from   TEXT,
    updated_at     TEXT
);
"""


@dataclass(frozen=True)
class Preference:
    key: str
    value: str
    kind: str
    observations: int
    contradictions: int
    learned_from: str
    updated_at: str

    @property
    def confidence(self) -> float:
        total = self.observations + self.contradictions
        return self.observations / total if total else 0.0

    @property
    def active(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS and self.confidence >= 0.6


class PreferenceStore:
    def __init__(self, db_path: str | Path):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(p), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- learning ----------------------------------------------------------

    def observe(self, key: str, value: str, *, kind: str = "path",
                learned_from: str = "") -> Preference:
        key = key.strip().lower()
        existing = self.get(key)
        now = datetime.now(timezone.utc).isoformat()
        if existing is None:
            self._conn.execute(
                "INSERT INTO preferences (key, value, kind, observations,"
                " contradictions, learned_from, updated_at) VALUES (?,?,?,1,0,?,?)",
                (key, value, kind, learned_from, now),
            )
        elif existing.value == value:
            self._conn.execute(
                "UPDATE preferences SET observations = observations + 1,"
                " updated_at = ? WHERE key = ?", (now, key)
            )
        else:
            # A different value for the same key is evidence against the old one.
            # Only once the contradictions outweigh the observations does the
            # preference flip — one unusual instruction must not rewrite a habit.
            self._conn.execute(
                "UPDATE preferences SET contradictions = contradictions + 1,"
                " updated_at = ? WHERE key = ?", (now, key)
            )
            refreshed = self.get(key)
            if refreshed and refreshed.contradictions > refreshed.observations:
                self._conn.execute(
                    "UPDATE preferences SET value = ?, observations = 1,"
                    " contradictions = 0, learned_from = ?, updated_at = ?"
                    " WHERE key = ?", (value, learned_from, now, key)
                )
        self._conn.commit()
        return self.get(key)  # type: ignore[return-value]

    def learn_from_plan(self, instruction: str, plan) -> list[Preference]:
        """Extract (noun -> destination) pairs from a plan the user approved."""
        from maestro.nlp.entities import FILE_TYPES

        learned: list[Preference] = []
        dests = [a.args.get("dest_dir") for a in plan.actions
                 if a.verb in ("fs.move_batch", "fs.copy_batch")
                 and isinstance(a.args.get("dest_dir"), str)]
        if not dests:
            return learned
        import re

        low = instruction.lower()
        # Longest match first, on a word boundary. Substring matching keyed
        # "move the invoices" to "invoice", so the preference never fired again
        # for the plural the user actually types.
        for noun in sorted(FILE_TYPES, key=len, reverse=True):
            if len(noun) <= 3:
                continue
            if re.search(rf"\b{re.escape(noun)}\b", low):
                learned.append(self.observe(noun, dests[0], kind="path",
                                            learned_from=instruction[:120]))
                break
        return learned

    # -- reading -----------------------------------------------------------

    def get(self, key: str) -> Preference | None:
        row = self._conn.execute(
            "SELECT key, value, kind, observations, contradictions,"
            " COALESCE(learned_from,''), COALESCE(updated_at,'')"
            " FROM preferences WHERE key = ?", (key.strip().lower(),)
        ).fetchone()
        return Preference(*row) if row else None

    def all(self) -> list[Preference]:
        return [Preference(*r) for r in self._conn.execute(
            "SELECT key, value, kind, observations, contradictions,"
            " COALESCE(learned_from,''), COALESCE(updated_at,'')"
            " FROM preferences ORDER BY observations DESC, key"
        )]

    def known_paths(self) -> dict[str, str]:
        """The gazetteer handed to the entity extractor — active prefs only."""
        return {p.key: p.value for p in self.all() if p.active and p.kind == "path"}

    # -- user control (FR-53) ----------------------------------------------

    def set(self, key: str, value: str, kind: str = "path") -> Preference:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO preferences (key, value, kind, observations,"
            " contradictions, learned_from, updated_at)"
            " VALUES (?,?,?,?,0,'set by user',?)",
            (key.strip().lower(), value, kind, MIN_OBSERVATIONS, now),
        )
        self._conn.commit()
        return self.get(key)  # type: ignore[return-value]

    def forget(self, key: str) -> bool:
        cur = self._conn.execute("DELETE FROM preferences WHERE key = ?",
                                 (key.strip().lower(),))
        self._conn.commit()
        return cur.rowcount > 0

    def clear(self) -> None:
        self._conn.execute("DELETE FROM preferences")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
