
"""
CommandExecutor: runs shell commands with real-time stdout/stderr streaming.
"""
from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from pathlib import Path


_DESTRUCTIVE = [
    "rm -rf /", "rm -rf ~", "rm -rf *", "sudo ", "mkfs", "dd if=",
    "shutdown", "reboot", "halt", "poweroff", ":(){ :|:& };:",
]


def _is_destructive(cmd: str) -> bool:
    lower = cmd.lower()
    return any(pat in lower for pat in _DESTRUCTIVE)


class CommandExecutor:
    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    async def run_streaming(
        self,
        command: str,
        on_chunk: Callable[[str], None],
        timeout: int = 300,
    ) -> int:
        """
        Run `command` in the project directory, calling on_chunk for each
        line of combined stdout+stderr. Returns the exit code.
        """
        if _is_destructive(command):
            on_chunk("[AURA SEC] Destructive command blocked.\n")
            return 126

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self._cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            async def _read():
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    result = on_chunk(line.decode(errors="replace"))
                    if asyncio.iscoroutine(result):
                        await result

            await asyncio.wait_for(_read(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            result = on_chunk(f"\n[AURA] Command killed — exceeded {timeout}s timeout.\n")
            if asyncio.iscoroutine(result):
                await result
            return 124

        await proc.wait()
        return proc.returncode or 0

    def run_sync(self, command: str, timeout: int = 120) -> tuple[int, str, str]:
        """Blocking run — used for audit/test reporting."""
        if _is_destructive(command):
            return 126, "", "[AURA SEC] Destructive command blocked."
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(self._cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"Command timed out after {timeout}s"
        except Exception as exc:
            return 1, "", str(exc)
