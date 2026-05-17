from __future__ import annotations

from pathlib import Path

from godotter.utils.textio import read_text_utf8, write_text_utf8


class EnvFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def set(self, key: str, value: str) -> None:
        lines = self._read_lines()
        rendered = f'{key}={value}'
        updated: list[str] = []
        replaced = False

        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith(f'{key}='):
                if not replaced:
                    updated.append(rendered)
                    replaced = True
                continue
            updated.append(line)

        if not replaced:
            updated.append(rendered)

        write_text_utf8(self.path, '\n'.join(updated).rstrip() + '\n')

    def _read_lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return read_text_utf8(self.path).splitlines()

