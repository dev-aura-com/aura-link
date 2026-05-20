"""
Aura Link CLI — entry point.

Usage:
  aura-link login   [--url URL]                         # Device-flow auth (no manual token needed)
  aura-link connect [--token TOKEN] [--url URL] [--dir PATH]
  aura-link logout

Note: `aura-link chat` is disabled for v1. Re-enable in a future release.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
import webbrowser
from pathlib import Path

import click
import httpx

from aura_link import credentials as cred_store

_DEFAULT_SERVER = os.environ.get("AURA_SERVER", "https://api.dev-aura.com")
_DEFAULT_WEB_UI = os.environ.get("AURA_WEB_UI", "https://dev-aura.com")
_CONFIG_FILE = ".aura-link.json"
_POLL_INTERVAL = 2   # seconds between /verify polls
_POLL_TIMEOUT = 300  # seconds before giving up (5 min)


# ── Config helpers ────────────────────────────────────────────────────────────

def _load_config(root: Path) -> dict:
    cfg_path = root / _CONFIG_FILE
    if cfg_path.is_file():
        try:
            return json.loads(cfg_path.read_text())
        except Exception:
            pass
    return {}


def _resolve_token(token: str | None, cfg: dict) -> str | None:
    """Priority: --token flag > env var > .aura-link.json > ~/.config/aura/credentials"""
    token = token or cfg.get("token") or os.environ.get("AURA_TOKEN")
    if not token:
        saved = cred_store.load()
        if saved:
            token = saved.get("token")
    return token


def _do_login(server: str, web_url: str | None = None) -> str | None:
    """Run the device-flow login and return the new token, or None on failure."""
    web = web_url or os.environ.get("AURA_WEB_UI", _DEFAULT_WEB_UI)
    session_id = str(uuid.uuid4())

    try:
        r = httpx.post(f"{server}/api/link/device/init", json={"session_id": session_id}, timeout=10)
        r.raise_for_status()
    except Exception as exc:
        click.echo(f"\033[31mCannot reach backend at {server}: {exc}\033[0m", err=True)
        return None

    auth_url = f"{web}/auth/cli?session_id={session_id}"
    click.echo(f"\n\033[36m[AURA LINK]\033[0m Opening browser for authorization...")
    click.echo(f"  URL: {auth_url}\n")
    click.echo("Waiting for you to click \033[33mAuthorize\033[0m in the browser...", nl=False)

    if not webbrowser.open(auth_url):
        click.echo(f"\n\033[33mCould not open browser automatically. Visit:\033[0m\n  {auth_url}")

    deadline = time.monotonic() + _POLL_TIMEOUT
    token: str | None = None
    refresh_token: str | None = None

    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL)
        click.echo(".", nl=False)
        try:
            resp = httpx.get(f"{server}/api/link/verify/{session_id}", timeout=8)
            if resp.status_code == 404:
                click.echo(f"\n\033[31mSession expired or not found.\033[0m", err=True)
                return None
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "authorized":
                    token = data["token"]
                    refresh_token = data.get("refresh_token")
                    break
        except httpx.RequestError:
            pass

    if not token:
        click.echo(f"\n\033[31mTimed out waiting for authorization ({_POLL_TIMEOUT}s).\033[0m", err=True)
        return None

    cred_store.save(token, server, refresh_token)
    click.echo(f"\n\n\033[32m✓ Logged in successfully!\033[0m")
    return token


def _try_silent_refresh(server: str, refresh_token: str) -> tuple[str, str] | None:
    """Exchange a refresh token for a new (access_token, refresh_token) pair silently.

    Uses token rotation: the old refresh token is revoked server-side and a new
    one is returned. Returns None if the refresh token is expired or invalid.
    """
    try:
        resp = httpx.post(
            f"{server}/api/link/token/refresh",
            json={"refresh_token": refresh_token},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data["access_token"], data["refresh_token"]
    except Exception:
        pass
    return None


def _resolve_server(url: str | None, cfg: dict) -> str:
    if url:
        return url
    saved = cred_store.load()
    if saved and saved.get("server"):
        return saved["server"]
    return cfg.get("server") or os.environ.get("AURA_SERVER", _DEFAULT_SERVER)


def _ws_url(http_url: str) -> str:
    return http_url.replace("http://", "ws://").replace("https://", "wss://")


# ── CLI group ─────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("1.0.0")
def main() -> None:
    """Aura Link — local execution bridge for the Aura Web Command Center."""


# ── login ─────────────────────────────────────────────────────────────────────

@main.command()
@click.option("--url", envvar="AURA_SERVER", default=None, help="Backend server URL (default: https://api.dev-aura.com)")
@click.option("--web", "web_url", default=None, help="Web UI URL (default: https://dev-aura.com)")
def login(url: str | None, web_url: str | None) -> None:
    """Authenticate via browser — no manual token copying required."""
    server = url or os.environ.get("AURA_SERVER", _DEFAULT_SERVER)
    token = _do_login(server, web_url)
    if not token:
        sys.exit(1)
    click.echo(f"  Credentials saved to \033[36m{cred_store._CRED_FILE}\033[0m")
    click.echo(f"  Run \033[33maura-link connect\033[0m in any project directory to start.")


# ── logout ────────────────────────────────────────────────────────────────────

@main.command()
def logout() -> None:
    """Remove saved credentials from this machine."""
    cred_store.clear()
    click.echo("\033[32mLogged out — credentials removed.\033[0m")


# ── connect ───────────────────────────────────────────────────────────────────

@main.command()
@click.option("--token", envvar="AURA_TOKEN", default=None, help="JWT access token (auto-loaded after `aura-link login`)")
@click.option("--url", envvar="AURA_SERVER", default=None, help="Backend server URL")
@click.option("--dir", "project_dir", default=None, type=click.Path(exists=True, file_okay=False))
@click.option("--quiet", is_flag=True, default=False)
def connect(token: str | None, url: str | None, project_dir: str | None, quiet: bool) -> None:
    """Connect this directory to the Aura War Room and watch for instructions."""
    from aura_link.connection import AuraLinkClient, AuthError

    root = Path(project_dir).resolve() if project_dir else Path.cwd()
    cfg = _load_config(root)
    server = _resolve_server(url, cfg)
    token = _resolve_token(token, cfg)

    if not token:
        click.echo("\033[33m[AURA LINK]\033[0m No credentials found.", err=True)
        if not click.confirm("Would you like to login now?", default=True):
            sys.exit(1)
        token = _do_login(server)
        if not token:
            sys.exit(1)

    while True:
        client = AuraLinkClient(
            server_url=_ws_url(server),
            token=token,
            project_root=root,
            quiet=quiet,
        )
        try:
            asyncio.run(client.run())
            break
        except AuthError:
            # Try silent refresh before bothering the user
            saved = cred_store.load()
            stored_refresh = saved.get("refresh_token") if saved else None
            if stored_refresh:
                result = _try_silent_refresh(server, stored_refresh)
                if result:
                    token, new_refresh = result
                    cred_store.save(token, server, new_refresh)
                    continue  # retry WS connection with fresh token

            # Refresh failed or no refresh token — need full login
            click.echo("\n\033[33m[AURA LINK]\033[0m Your session has expired or the token is invalid.")
            if not click.confirm("Would you like to login again?", default=True):
                break
            token = _do_login(server)
            if not token:
                break
        except KeyboardInterrupt:
            click.echo("\n[AURA LINK] Disconnected.")
            break


# ── chat (v2 — disabled for v1 launch) ────────────────────────────────────────

# @main.command()
# @click.option("--token", envvar="AURA_TOKEN", default=None)
# @click.option("--url", envvar="AURA_SERVER", default=None)
# @click.option("--task-id", default=None, help="Task ID for War Room sync")
# @click.option("--dir", "project_dir", default=None, type=click.Path(exists=True, file_okay=False))
# def chat(token, url, task_id, project_dir):
#     """(v2) Interactive AI chat — patches files locally and syncs to War Room."""
#     ...  # Re-enable when CLI chat ships
