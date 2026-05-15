"""
FileWatcher: monitors the project directory and fires a callback on changes.
Uses the watchdog library.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from aura_link.secret_filter import SecretFilter


_IGNORE_DIRS = frozenset({
    "node_modules", ".git", ".venv", "__pycache__", "dist",
    ".svelte-kit", ".next", "build", ".cache", ".tox", ".mypy_cache",
})


class _Handler(FileSystemEventHandler):
    def __init__(self, root: Path, callback: Callable[[str, str], None]) -> None:
        super().__init__()
        self._root = root
        self._filter = SecretFilter(root)
        self._callback = callback

    def _should_skip(self, path: str) -> bool:
        p = Path(path)
        for part in p.parts:
            if part in _IGNORE_DIRS:
                return True
        if self._filter.is_secret(path):
            return True
        return False

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._should_skip(event.src_path):
            return
        try:
            rel = str(Path(event.src_path).relative_to(self._root))
        except ValueError:
            rel = event.src_path
        self._callback("modified", rel)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._should_skip(event.src_path):
            return
        try:
            rel = str(Path(event.src_path).relative_to(self._root))
        except ValueError:
            rel = event.src_path
        self._callback("created", rel)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._should_skip(event.src_path):
            return
        try:
            rel = str(Path(event.src_path).relative_to(self._root))
        except ValueError:
            rel = event.src_path
        self._callback("deleted", rel)


class FileWatcher:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._observer: Observer | None = None

    def start(self, callback: Callable[[str, str], None]) -> None:
        handler = _Handler(self._root, callback)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
