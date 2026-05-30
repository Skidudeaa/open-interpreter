"""
Tests for autojump-style directory frecency (DirectoryFrecency + extract_cd_targets).
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from interpreter.core.memory.directory_frecency import (  # noqa: E402
    DirectoryFrecency,
    extract_cd_targets,
)


class TestExtractCdTargets:
    def test_shell_cd(self):
        assert extract_cd_targets("cd /tmp/foo", "shell") == ["/tmp/foo"]

    def test_shell_pushd_and_chained(self):
        targets = extract_cd_targets("mkdir x && cd x; pushd /var", "bash")
        assert "x" in targets and "/var" in targets

    def test_shell_quoted_path_with_space(self):
        assert extract_cd_targets('cd "/tmp/a b"', "shell") == ['"/tmp/a b"']

    def test_skips_cd_dash_and_bare_cd(self):
        assert extract_cd_targets("cd -", "shell") == []
        assert extract_cd_targets("cd\n", "shell") == []

    def test_python_chdir(self):
        assert extract_cd_targets("os.chdir('/tmp/zzz')", "python") == ["/tmp/zzz"]

    def test_no_false_positive(self):
        assert extract_cd_targets("print('according to plan')", "python") == []

    def test_empty_code(self):
        assert extract_cd_targets("", "shell") == []


class TestDirectoryFrecency:
    def _store(self):
        # In-memory DB keeps tests isolated and fast.
        return DirectoryFrecency(db_path=None)

    def test_record_and_query_existing_dir(self):
        store = self._store()
        with tempfile.TemporaryDirectory() as d:
            resolved = store.record(d)
            assert resolved == os.path.normpath(d)
            assert store.query(os.path.basename(d)) == os.path.normpath(d)

    def test_nonexistent_dir_not_recorded(self):
        store = self._store()
        assert store.record("/definitely/not/a/real/dir/xyz") is None
        assert store.query("xyz") is None

    def test_weight_grows_with_autojump_formula(self):
        store = self._store()
        with tempfile.TemporaryDirectory() as d:
            store.record(d)
            first = store.top(1)[0][1]
            store.record(d)
            second = store.top(1)[0][1]
            # autojump: w -> sqrt(w^2 + 100); 10 then sqrt(200) ~= 14.14
            assert abs(first - 10.0) < 1e-6
            assert abs(second - (200**0.5)) < 1e-6

    def test_frecency_ranking_prefers_more_visited(self):
        store = self._store()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            store.record(a)
            for _ in range(3):
                store.record(b)
            # Empty pattern returns the single highest-ranked dir.
            assert store.query("") == os.path.normpath(b)

    def test_basename_match_beats_deep_path_match(self):
        store = self._store()
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "needle")
            decoy = os.path.join(root, "needle", "haystack")
            os.makedirs(decoy)
            store.record(decoy)
            store.record(target)
            assert store.query("needle") == os.path.normpath(target)

    def test_relative_target_resolved_against_base(self):
        store = self._store()
        with tempfile.TemporaryDirectory() as base:
            sub = os.path.join(base, "child")
            os.makedirs(sub)
            assert store.record("child", base_cwd=base) == os.path.normpath(sub)

    def test_stale_dir_pruned_on_query(self):
        store = self._store()
        tmp = tempfile.mkdtemp()
        store.record(tmp)
        os.rmdir(tmp)
        assert store.query(os.path.basename(tmp)) is None
        assert store.top(10) == []

    def test_persists_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "jump.db")
            target = tempfile.mkdtemp()
            try:
                DirectoryFrecency(db).record(target)
                # Fresh instance, same file — entry survives.
                assert DirectoryFrecency(db).query(
                    os.path.basename(target)
                ) == os.path.normpath(target)
            finally:
                os.rmdir(target)

    def test_bad_db_path_falls_back_to_memory(self):
        # A path under a non-writable/again-file parent must not raise.
        store = DirectoryFrecency(db_path="/proc/cannot/write/here/jump.db")
        with tempfile.TemporaryDirectory() as d:
            assert store.record(d) == os.path.normpath(d)
