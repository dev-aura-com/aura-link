"""
AuraLinkClient: persistent WebSocket connection to the Aura backend.

Handles all inbound commands from the server (READ_FILE, WRITE_FILE, APPLY_PATCH,
DELETE_FILE, RUN_COMMAND, RUN_AUDIT, PING) and dispatches responses.

Note: CHAT_REQUEST / send_chat_message are disabled for v1. Re-enable for CLI chat (v2).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from aura_link.command_exec import CommandExecutor
from aura_link.file_ops import FileOps
from aura_link.watcher import FileWatcher

RECONNECT_DELAY = 5  # seconds between reconnect attempts
PING_INTERVAL = 20   # seconds between keepalive pings


class AuthError(Exception):
    """Raised when the server rejects the WebSocket connection with HTTP 403."""


class AuraLinkClient:
    def __init__(
        self,
        server_url: str,
        token: str,
        project_root: Path,
        *,
        quiet: bool = False,
        # on_chat_response: Any = None,  # v2 — re-enable for CLI chat
    ) -> None:
        self._url = server_url
        self._token = token
        self._root = project_root.resolve()
        self._file_ops = FileOps(self._root)
        self._executor = CommandExecutor(self._root)
        self._watcher = FileWatcher(self._root)
        self._quiet = quiet
        self._ws: Any = None
        self._session_id: str | None = None
        self._stop = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Logging ──────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        if not self._quiet:
            print(f"\033[36m[AURA LINK]\033[0m {msg}", flush=True)

    def _err(self, msg: str) -> None:
        print(f"\033[31m[AURA LINK ERROR]\033[0m {msg}", file=sys.stderr, flush=True)

    # ── Sending ───────────────────────────────────────────────────────────────

    async def _send(self, msg: dict) -> None:
        if self._ws:
            await self._ws.send(json.dumps(msg))

    # ── File watcher callback ─────────────────────────────────────────────────

    def _on_file_change(self, event_type: str, rel_path: str) -> None:
        self._log(f"File {event_type}: {rel_path}")
        if self._ws and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send({
                    "type": "FILE_CHANGED",
                    "event": event_type,
                    "path": rel_path,
                }),
                self._loop,
            )

    # ── Command handlers ──────────────────────────────────────────────────────

    async def _handle(self, msg: dict) -> None:
        msg_type = msg.get("type", "")
        request_id = msg.get("request_id", str(uuid.uuid4())[:8])

        if msg_type == "PING":
            await self._send({"type": "PONG"})
            return

        if msg_type == "WELCOME":
            self._session_id = msg.get("session_id")
            self._log(f"Session established: {self._session_id}")
            return

        if msg_type == "READ_FILE":
            path = msg.get("path", "")
            try:
                content = self._file_ops.read(path)
                await self._send({"type": "RESPONSE", "request_id": request_id, "ok": True, "content": content, "path": path})
            except Exception as exc:
                await self._send({"type": "RESPONSE", "request_id": request_id, "ok": False, "error": str(exc)})

        elif msg_type == "WRITE_FILE":
            path = msg.get("path", "")
            content = msg.get("content", "")
            try:
                self._file_ops.write(path, content)
                self._log(f"Wrote: {path}")
                await self._send({"type": "RESPONSE", "request_id": request_id, "ok": True, "path": path})
            except Exception as exc:
                await self._send({"type": "RESPONSE", "request_id": request_id, "ok": False, "error": str(exc)})

        elif msg_type == "APPLY_PATCH":
            path = msg.get("path", "")
            old = msg.get("old", "")
            new = msg.get("new", "")
            try:
                self._file_ops.apply_patch(path, old, new)
                self._log(f"Patched: {path}")
                await self._send({"type": "RESPONSE", "request_id": request_id, "ok": True, "path": path})
            except Exception as exc:
                await self._send({"type": "RESPONSE", "request_id": request_id, "ok": False, "error": str(exc)})

        elif msg_type == "DELETE_FILE":
            path = msg.get("path", "")
            try:
                self._file_ops.delete(path)
                self._log(f"Deleted: {path}")
                await self._send({"type": "RESPONSE", "request_id": request_id, "ok": True, "path": path})
            except Exception as exc:
                await self._send({"type": "RESPONSE", "request_id": request_id, "ok": False, "error": str(exc)})

        elif msg_type == "RUN_COMMAND":
            command = msg.get("command", "")
            self._log(f"$ {command}")
            chunks: list[str] = []

            async def on_chunk(text: str) -> None:
                chunks.append(text)
                sys.stdout.write(text)
                sys.stdout.flush()
                await self._send({"type": "STREAM_CHUNK", "request_id": request_id, "data": text})

            exit_code = await self._executor.run_streaming(command, on_chunk)
            output = "".join(chunks)
            await self._send({
                "type": "RESPONSE",
                "request_id": request_id,
                "ok": exit_code == 0,
                "exit_code": exit_code,
                "output": output[-4000:],
            })

        elif msg_type == "RUN_AUDIT":
            task_id = msg.get("task_id", "")
            self._log("Running audit / test suite locally...")
            # Detect the nearest subdirectory that contains a toolchain file,
            # so `npm test` / `pytest` run from the right project root.
            _audit_cwd = self._executor._cwd
            for _marker in ("package.json", "requirements.txt", "go.mod", "Cargo.toml"):
                _hits = sorted(
                    (p for p in _audit_cwd.rglob(_marker) if "node_modules" not in p.parts),
                    key=lambda p: len(p.parts),
                )
                if _hits:
                    _audit_cwd = _hits[0].parent
                    break
            import shlex
            _cd = f"cd {shlex.quote(str(_audit_cwd))} && " if _audit_cwd != self._executor._cwd else ""
            exit_code, stdout, stderr = self._executor.run_sync(
                f"{_cd}python -m pytest --tb=short -q --no-header 2>&1 || npm test 2>&1 || echo 'No test suite found'",
                timeout=120,
            )
            logs = (stdout + stderr)[-3000:]
            await self._send({
                "type": "RESPONSE",
                "request_id": request_id,
                "ok": True,
                "exit_code": exit_code,
                "logs": logs,
                "task_id": task_id,
            })

        # elif msg_type == "CHAT_REQUEST":  # v2 — disabled for v1 launch
        #     if self._on_chat_response:
        #         text = msg.get("text", "")
        #         patches = msg.get("patches", [])
        #         await self._on_chat_response(text, patches, request_id)
        #     else:
        #         await self._send({"type": "RESPONSE", "request_id": request_id,
        #                           "ok": False, "error": "CLI not in chat mode"})

    # ── Main connection loop ──────────────────────────────────────────────────

    async def run(self) -> None:
        self._loop = asyncio.get_event_loop()
        ws_url = (
            self._url.rstrip("/")
            + f"/api/link/ws?token={self._token}"
        )
        self._watcher.start(self._on_file_change)
        self._log(f"Watching: {self._root}")

        try:
            while not self._stop:
                try:
                    self._log(f"Connecting to {self._url}...")
                    async with websockets.connect(
                        ws_url,
                        ping_interval=PING_INTERVAL,
                        ping_timeout=30,
                        open_timeout=15,
                    ) as ws:
                        self._ws = ws
                        self._log("Connected. Waiting for instructions from the War Room.")

                        await self._send({
                            "type": "HELLO",
                            "version": "1.0.0",
                            "cwd": str(self._root),
                            "platform": sys.platform,
                        })

                        async for raw in ws:
                            try:
                                msg = json.loads(raw)
                                await self._handle(msg)
                            except json.JSONDecodeError:
                                self._err(f"Malformed message: {raw[:200]}")
                            except Exception as exc:
                                self._err(f"Handler error: {exc}")

                except (ConnectionClosedOK, ConnectionClosedError):
                    self._log("Disconnected.")
                except Exception as exc:
                    if "403" in str(exc):
                        self._err("Authentication failed (HTTP 403) — token may be expired or invalid.")
                        raise AuthError(str(exc)) from exc
                    self._err(f"Connection error: {exc}")
                finally:
                    self._ws = None

                if not self._stop:
                    self._log(f"Reconnecting in {RECONNECT_DELAY}s...")
                    await asyncio.sleep(RECONNECT_DELAY)
        finally:
            self._watcher.stop()

    def stop(self) -> None:
        self._stop = True

    # async def send_chat_message(self, text: str, task_id: str | None) -> None:  # v2
    #     await self._send({
    #         "type": "CHAT_MESSAGE", "text": text, "task_id": task_id or "",
    #         "cwd": str(self._root), "request_id": str(uuid.uuid4())[:8],
    #     })
