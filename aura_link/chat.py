"""
ChatSession: interactive `aura chat` mode.

The user types at the terminal → CLI sends CHAT_MESSAGE to backend → backend
returns CHAT_RESPONSE with text + optional patches → CLI applies patches and
prints the response. The transcript is synced to the War Room DB by the backend.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aura_link.connection import AuraLinkClient

_PATCH_BLOCK_RE = re.compile(
    r"PATCH_START:(.+?)\n<<<OLD\n(.*?)OLD>>>\n<<<NEW\n(.*?)NEW>>>\nPATCH_END",
    re.DOTALL,
)


def _print_response(text: str) -> None:
    print(f"\n\033[32m[AURA]\033[0m {text}\n", flush=True)


def _apply_patches_from_response(text: str, root: Path) -> str:
    """Parse PATCH blocks from the LLM response and apply them to local files.

    Returns the display text with patch blocks stripped.
    """
    from aura_link.file_ops import FileOps
    file_ops = FileOps(root)
    display = text

    for match in _PATCH_BLOCK_RE.finditer(text):
        path = match.group(1).strip()
        old = match.group(2)
        new = match.group(3)
        try:
            file_ops.apply_patch(path, old, new)
            print(f"\033[33m[PATCH APPLIED]\033[0m {path}", flush=True)
        except Exception as exc:
            print(f"\033[31m[PATCH FAILED]\033[0m {path}: {exc}", flush=True)
        display = display.replace(match.group(0), f"[Patch applied to {path}]")

    return display.strip()


class ChatSession:
    def __init__(self, client: "AuraLinkClient", task_id: str | None, root: Path) -> None:
        self._client = client
        self._task_id = task_id
        self._root = root
        self._response_event: asyncio.Event = asyncio.Event()
        self._pending_response: str = ""
        self._pending_patches: list[dict] = []

    async def on_response(self, text: str, patches: list[dict], request_id: str) -> None:
        """Called by the connection layer when a CHAT_REQUEST response arrives."""
        # Apply server-computed patches first
        for patch in patches:
            from aura_link.file_ops import FileOps
            try:
                FileOps(self._root).apply_patch(
                    patch["path"], patch["old"], patch["new"]
                )
                print(f"\033[33m[PATCH APPLIED]\033[0m {patch['path']}", flush=True)
            except Exception as exc:
                print(f"\033[31m[PATCH FAILED]\033[0m {patch.get('path')}: {exc}", flush=True)

        # Apply inline patch blocks embedded in the text
        display = _apply_patches_from_response(text, self._root)
        self._pending_response = display
        self._response_event.set()

    async def run(self) -> None:
        # Register ourselves as the chat response handler
        self._client._on_chat_response = self.on_response  # type: ignore[attr-defined]

        print("\033[36m[AURA CHAT]\033[0m Interactive mode. Type your message and press Enter.")
        print("  Commands: /quit to exit, /task <id> to switch task context\n", flush=True)

        loop = asyncio.get_event_loop()

        while True:
            try:
                prompt = f"\033[33m({self._task_id or 'no task'})\033[0m > "
                user_input = await loop.run_in_executor(None, lambda: input(prompt))
            except (EOFError, KeyboardInterrupt):
                print("\n[AURA CHAT] Goodbye.", flush=True)
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input == "/quit":
                print("[AURA CHAT] Goodbye.", flush=True)
                break

            if user_input.startswith("/task "):
                self._task_id = user_input[6:].strip()
                print(f"[AURA CHAT] Task context set to: {self._task_id}", flush=True)
                continue

            self._response_event.clear()
            self._pending_response = ""

            await self._client.send_chat_message(user_input, self._task_id)

            try:
                await asyncio.wait_for(self._response_event.wait(), timeout=60.0)
                _print_response(self._pending_response)
            except asyncio.TimeoutError:
                print("\033[31m[AURA CHAT]\033[0m Request timed out. The server may be busy.", flush=True)
