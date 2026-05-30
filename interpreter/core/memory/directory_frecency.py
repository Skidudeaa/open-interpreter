"""
DirectoryFrecency - autojump-style "frecency" ranking of visited directories.

Ports autojump's well-tuned weighting (https://github.com/wting/autojump):
each visit does ``weight = sqrt(weight**2 + 100)`` so a directory's score
encodes BOTH how often and how recently it was used. The interpreter records
``cd`` / ``os.chdir`` targets from executed code blocks (autojump hooks the
shell prompt; we can't, because Open Interpreter's shell runs as a piped
subprocess, so we parse the code instead), then ``Files.jump("partial")``
resolves the highest-ranked directory matching a substring.

Storage mirrors SemanticEditGraph's SQLite pattern: a real file path with an
in-memory fallback so a bad/locked path can never break code execution.
"""

import math
import os
import re
import sqlite3
from pathlib import Path

# WHY: autojump's increment is sqrt(w**2 + 10**2). 10 is the default weight
# added per visit; squaring-then-rooting makes early visits matter a lot and
# later ones taper, which is what makes "frecency" feel right in practice.
_VISIT_INCREMENT_SQ = 100.0

# Targets we never want to record: bare `cd` (-> $HOME spam) and `cd -` (toggle).
_SKIP_TARGETS = {"", "-", "~", "$HOME", "${HOME}"}

# Shell: `cd DIR`, `pushd DIR` at a statement boundary. We capture the first
# token after the command; quote stripping happens in _normalize().
_SHELL_CD_RE = re.compile(
    r"""(?:^|[;&|]|&&|\|\||\bthen\b|\bdo\b)\s*
        (?:cd|pushd)\s+
        (?!-(?:\s|$))               # skip `cd -` (toggle), incl. end-of-line
        (?P<target>"[^"]+"|'[^']+'|[^\s;&|]+)""",
    re.VERBOSE | re.MULTILINE,
)

# Python: os.chdir("DIR") / os.chdir('DIR') (also pathlib-free chdir alias).
_PY_CHDIR_RE = re.compile(r"""chdir\(\s*(?P<q>['"])(?P<target>.+?)(?P=q)\s*\)""")


def extract_cd_targets(code: str, language: str) -> list[str]:
    """
    Pull raw directory targets out of an executed code block.

    Returns un-resolved strings (may be relative, may contain ~/$HOME);
    resolution against a base cwd happens in DirectoryFrecency.record().
    """
    if not code:
        return []

    lang = (language or "").lower()
    targets: list[str] = []

    if lang in ("shell", "bash", "sh", "zsh", "powershell", "applescript"):
        # `cd ` / `pushd ` is the cheap pre-filter the hot path relies on.
        if "cd " in code or "pushd " in code:
            targets += [m.group("target") for m in _SHELL_CD_RE.finditer(code)]

    if lang == "python" or "chdir(" in code:
        targets += [m.group("target") for m in _PY_CHDIR_RE.finditer(code)]

    return targets


class DirectoryFrecency:
    """
    Persistent frecency-ranked directory index backing ``computer.files.jump``.

    NOTE: every public method swallows storage errors and degrades to a no-op /
    empty result. Filesystem navigation convenience must never be able to crash
    or block the interpreter's execution loop.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        self._connection: sqlite3.Connection | None = None
        try:
            if db_path:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                self._connection = sqlite3.connect(db_path, check_same_thread=False)
            else:
                self._connection = sqlite3.connect(":memory:", check_same_thread=False)
            self._create_schema()
        except (sqlite3.Error, OSError):
            # WHY: fall back to volatile memory rather than propagate — a
            # broken jump DB should degrade the feature, not the session.
            try:
                self._connection = sqlite3.connect(":memory:", check_same_thread=False)
                self._create_schema()
            except sqlite3.Error:
                self._connection = None

    def _create_schema(self) -> None:
        assert self._connection is not None
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS directories (
                path TEXT PRIMARY KEY,
                weight REAL NOT NULL,
                last_visit REAL NOT NULL
            )
            """
        )
        self._connection.commit()

    @staticmethod
    def _normalize(target: str, base_cwd: str) -> str | None:
        """Resolve a raw cd target to an absolute path that exists, or None."""
        t = target.strip().strip("\"'")
        if t in _SKIP_TARGETS:
            return None
        t = os.path.expanduser(os.path.expandvars(t))
        if not os.path.isabs(t):
            t = os.path.join(base_cwd or os.getcwd(), t)
        t = os.path.normpath(t)
        # Only index real directories; junk relative resolutions self-filter.
        return t if os.path.isdir(t) else None

    def record(self, target: str, base_cwd: str = "") -> str | None:
        """
        Record one visit. ``target`` may be raw (relative, ~, $VAR).

        Returns the resolved absolute path if recorded, else None.
        """
        if self._connection is None:
            return None
        resolved = self._normalize(target, base_cwd)
        if resolved is None:
            return None
        try:
            cur = self._connection.execute(
                "SELECT weight FROM directories WHERE path = ?", (resolved,)
            )
            row = cur.fetchone()
            old = row[0] if row else 0.0
            new_weight = math.sqrt(old * old + _VISIT_INCREMENT_SQ)
            now = _now()
            self._connection.execute(
                """
                INSERT INTO directories (path, weight, last_visit)
                VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET weight = ?, last_visit = ?
                """,
                (resolved, new_weight, now, new_weight, now),
            )
            self._connection.commit()
            return resolved
        except sqlite3.Error:
            return None

    def record_many(self, targets: list[str], base_cwd: str = "") -> list[str]:
        """Record several targets; returns the resolved paths actually stored."""
        out = []
        for t in targets:
            r = self.record(t, base_cwd)
            if r:
                out.append(r)
        return out

    def query(self, pattern: str = "") -> str | None:
        """
        Best directory whose path contains ``pattern`` (case-insensitive).

        Ranks by frecency weight, tie-broken by recency. Prefers matches in the
        final path component (autojump behavior: `j foo` should land on .../foo,
        not .../foo/deeply/nested/other). Stale (deleted) dirs are pruned lazily.
        """
        if self._connection is None:
            return None
        try:
            cur = self._connection.execute(
                "SELECT path, weight, last_visit FROM directories "
                "ORDER BY weight DESC, last_visit DESC"
            )
            rows = cur.fetchall()
        except sqlite3.Error:
            return None

        pat = pattern.lower().strip()
        basename_hit = None
        path_hit = None
        stale: list[str] = []

        for path, _weight, _lv in rows:
            if not os.path.isdir(path):
                stale.append(path)
                continue
            if not pat:
                self._purge(stale)
                return path
            if pat in os.path.basename(path).lower():
                basename_hit = path
                break
            if path_hit is None and pat in path.lower():
                path_hit = path

        self._purge(stale)
        return basename_hit or path_hit

    def top(self, n: int = 10) -> list[tuple[str, float]]:
        """Highest-ranked existing directories — for `%jump` listing / pre-prompt."""
        if self._connection is None:
            return []
        try:
            cur = self._connection.execute(
                "SELECT path, weight FROM directories "
                "ORDER BY weight DESC, last_visit DESC"
            )
            rows = cur.fetchall()
        except sqlite3.Error:
            return []
        result: list[tuple[str, float]] = []
        stale: list[str] = []
        for path, weight in rows:
            if os.path.isdir(path):
                result.append((path, weight))
                if len(result) >= n:
                    break
            else:
                stale.append(path)
        self._purge(stale)
        return result

    def _purge(self, paths: list[str]) -> None:
        if not paths or self._connection is None:
            return
        try:
            self._connection.executemany(
                "DELETE FROM directories WHERE path = ?", [(p,) for p in paths]
            )
            self._connection.commit()
        except sqlite3.Error:
            pass


def _now() -> float:
    import time

    return time.time()
