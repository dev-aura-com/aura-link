"""
FileOps: jailed file operations using pathlib.
All paths are resolved relative to the project root; traversal attempts raise PermissionError.
"""
from __future__ import annotations

from pathlib import Path

from aura_link.secret_filter import SecretFilter


class FileOps:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._filter = SecretFilter(self._root)

    def _resolve(self, rel: str) -> Path:
        # Reject any path the cloud sends that is already absolute. Python's /
        # operator silently discards self._root when the right-hand side is an
        # absolute path (Path("/safe/root") / "/etc/passwd" → Path("/etc/passwd")),
        # so we must catch this before the join, not after.
        if (
            rel.startswith("/")
            or rel.startswith("\\")
            or rel.startswith("~")
            or (len(rel) >= 2 and rel[1] == ":" and rel[0].isalpha())  # Windows drive: C:\...
        ):
            raise PermissionError(
                f"SECURITY: absolute path rejected: {rel!r}. "
                "All agent-issued paths must be relative to the project root."
            )
        target = (self._root / rel).resolve()
        try:
            target.relative_to(self._root)
        except ValueError:
            raise PermissionError(f"Path traversal blocked: {rel!r}")
        if self._filter.is_secret(target):
            raise PermissionError(f"SECRET_FILES: sending {rel!r} to the cloud is not allowed")
        return target

    def read(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"Not a file: {path!r}")
        return target.read_text(encoding="utf-8", errors="replace")

    def write(self, path: str, content: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def apply_patch(self, path: str, old: str, new: str) -> None:
        """Surgical in-place replacement of `old` with `new` inside a file."""
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"Not a file: {path!r}")
        content = target.read_text(encoding="utf-8", errors="replace")
        if old not in content:
            raise ValueError(f"Patch target not found in {path!r} — the file may have already changed")
        target.write_text(content.replace(old, new, 1), encoding="utf-8")

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            import shutil
            shutil.rmtree(target)
        else:
            raise FileNotFoundError(f"Not found: {path!r}")
