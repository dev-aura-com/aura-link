"""
Credential store for Aura Link.
Token is persisted to ~/.config/aura/credentials (JSON).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_CRED_DIR = Path.home() / ".config" / "aura"
_CRED_FILE = _CRED_DIR / "credentials"


def save(token: str, server: str = "https://api.dev-aura.com", refresh_token: str | None = None) -> None:
    _CRED_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {"token": token, "server": server}
    if refresh_token:
        data["refresh_token"] = refresh_token
    _CRED_FILE.write_text(json.dumps(data, indent=2))
    _CRED_FILE.chmod(0o600)


def load() -> dict | None:
    if not _CRED_FILE.is_file():
        return None
    try:
        data = json.loads(_CRED_FILE.read_text())
        if data.get("token"):
            return data
    except Exception:
        pass
    return None


def clear() -> None:
    if _CRED_FILE.is_file():
        _CRED_FILE.unlink()
