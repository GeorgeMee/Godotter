from __future__ import annotations

from pathlib import Path
import tempfile


def read_text_utf8(path: Path) -> str:
    return path.read_text(encoding='utf-8-sig')


def _normalize_newlines(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n')


def write_text_utf8(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_newlines(content)
    path.write_bytes(normalized.encode('utf-8'))


def atomic_write_text_utf8(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalize_newlines(content).encode('utf-8')
    with tempfile.NamedTemporaryFile('wb', delete=False, dir=path.parent) as handle:
        handle.write(payload)
        temp_name = handle.name
    Path(temp_name).replace(path)
