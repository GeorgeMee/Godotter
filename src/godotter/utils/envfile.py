from __future__ import annotations

from pathlib import Path


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

        self.path.write_text('\n'.join(updated).rstrip() + '\n', encoding='utf-8')

    def _read_lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return self.path.read_text(encoding='utf-8').splitlines()