"""
SecretFilter: blocks .env files and .gitignore patterns from being sent to the cloud.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

import pathspec

_ALWAYS_BLOCKED = frozenset({
    ".env", ".env.local", ".env.production", ".env.development",
    ".env.staging", ".env.test", "*.pem", "*.key", "*.p12",
    "id_rsa", "id_ed25519", ".netrc", "*.secret",
})

_ALWAYS_BLOCKED_GLOBS = [
    "**/.env", "**/.env.*", "**/*.pem", "**/*.key",
    "**/*.p12", "**/id_rsa", "**/id_ed25519",
]


class SecretFilter:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._spec = self._load_gitignore(root)

    @staticmethod
    def _load_gitignore(root: Path) -> pathspec.PathSpec | None:
        gi = root / ".gitignore"
        if gi.is_file():
            try:
                return pathspec.PathSpec.from_lines("gitwildmatch", gi.read_text().splitlines())
            except Exception:
                pass
        return None

    def is_secret(self, path: str | Path) -> bool:
        p = Path(path)
        name = p.name

        for pattern in _ALWAYS_BLOCKED:
            if fnmatch.fnmatch(name, pattern):
                return True

        rel = str(p.relative_to(self._root)) if p.is_absolute() else str(p)

        for glob in _ALWAYS_BLOCKED_GLOBS:
            if fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch("/" + rel, glob):
                return True

        if self._spec and self._spec.match_file(rel):
            return True

        return False
