from __future__ import annotations

from pathlib import Path


class Memory:
    """Persistent scratchpad for user preferences and task continuity."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text('# Godotter Memory\n\n', encoding='utf-8')
        self.content = self.path.read_text(encoding='utf-8')

    def save(self, content: str) -> None:
        self.content = content
        self.path.write_text(content, encoding='utf-8')