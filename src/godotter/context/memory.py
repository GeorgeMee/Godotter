from __future__ import annotations

from pathlib import Path

from godotter.utils.textio import read_text_utf8, write_text_utf8


class Memory:
    """Persistent scratchpad for user preferences and task continuity."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            write_text_utf8(self.path, '# Godotter Memory\n\n')
        self.content = read_text_utf8(self.path)

    def save(self, content: str) -> None:
        self.content = content
        write_text_utf8(self.path, content)

