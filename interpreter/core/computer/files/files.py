import difflib
import os

from ...utils.lazy_import import lazy_import

# Lazy import of aifs, imported when needed
aifs = lazy_import("aifs")


class Files:
    def __init__(self, computer):
        self.computer = computer
        # Lazy: the frecency DB is only opened the first time a directory is
        # recorded or a jump is attempted, so sessions that never navigate pay
        # nothing.
        self._frecency = None

    @property
    def frecency(self):
        if self._frecency is None:
            from ...memory.directory_frecency import DirectoryFrecency

            try:
                from ....terminal_interface.utils.local_storage_path import (
                    get_storage_path,
                )

                db_path = get_storage_path("jump.db")
            except Exception:
                db_path = None
            self._frecency = DirectoryFrecency(db_path)
        return self._frecency

    def search(self, *args, **kwargs):
        """
        Search the filesystem for the given query.
        """
        return aifs.search(*args, **kwargs)

    def jump(self, pattern: str = "") -> str:
        """
        Jump to the most-used directory whose path matches `pattern` (autojump-style frecency). Returns the new absolute working directory.
        """
        target = self.frecency.query(pattern)
        if target is None:
            raise FileNotFoundError(
                f"No remembered directory matches {pattern!r}. "
                "Directories are learned from `cd` / os.chdir as you work."
            )
        os.chdir(target)
        self.computer.cwd = target
        # Jumping is itself a strong usage signal — reinforce it.
        self.frecency.record(target)
        return target

    def record_visit(self, path: str) -> None:
        """
        Remember `path` as a visited directory for future jumps (does not change directory).
        """
        self.frecency.record(path, base_cwd=getattr(self.computer, "cwd", ""))

    def edit(self, path, original_text, replacement_text):
        """
        Edits a file on the filesystem, replacing the original text with the replacement text.
        """
        with open(path) as file:
            filedata = file.read()

        if original_text not in filedata:
            matches = get_close_matches_in_text(original_text, filedata)
            if matches:
                suggestions = ", ".join(matches)
                raise ValueError(
                    f"Original text not found. Did you mean one of these? {suggestions}"
                )

        filedata = filedata.replace(original_text, replacement_text)

        with open(path, "w") as file:
            file.write(filedata)


def get_close_matches_in_text(original_text, filedata, n=3):
    """
    Returns the closest matches to the original text in the content of the file.
    """
    words = filedata.split()
    original_words = original_text.split()
    len_original = len(original_words)

    matches = []
    for i in range(len(words) - len_original + 1):
        phrase = " ".join(words[i : i + len_original])
        similarity = difflib.SequenceMatcher(None, original_text, phrase).ratio()
        matches.append((similarity, phrase))

    matches.sort(reverse=True)
    return [match[1] for match in matches[:n]]
