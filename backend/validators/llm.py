__all__ = (
    "LLMModule",
    "SimulatorProvider",
)

import asyncio
import signal
import os
import sys
import dataclasses
import logging
import aiohttp
from pathlib import Path
import json
import contextlib
import re

from dotenv import load_dotenv

load_dotenv()

from .base import *


logger = logging.getLogger(__name__)


ERROR_RE = re.compile(
    r'"code":\s*Str\("([^"]+)"\),\s*"message":\s*Str\("((?:[^"\\]|\\.)*)"\)',
    re.DOTALL,
)


def extract_error_message(stdout: str) -> str:
    """Extract relevant error message from GenVM stdout."""
    try:
        # Look for JSON-like error structure in the output
        # Pattern to match: "code": Str("error_code"), "message": Str("error message")
        match = ERROR_RE.search(stdout)

        if match:
            error_code = match.group(1)
            error_message = match.group(2)
            return f'code: "{error_code}", message: "{error_message}"'

        # Fallback: if no structured error found, return a truncated version
        if len(stdout) > 500:
            return stdout[:500] + "... [truncated]"
        return stdout

    except Exception:
        # If parsing fails, return truncated version
        if len(stdout) > 500:
            return stdout[:500] + "... [truncated]"
        return stdout


@dataclasses.dataclass
class SimulatorProvider:
    id: str
    model: str
    url: str
    plugin: str
    key_env: str


class LLMModule:
    _process: asyncio.subprocess.Process | None

    def __init__(self):
        self.address = f"127.0.0.1:3032"

        self._terminated = False

        self._process = None
        self._restart_lock = asyncio.Lock()

        greyboxing_path = Path(__file__).parent.joinpath("greyboxing.lua")

        self._config = ChangedConfigFile("genvm-module-llm.yaml")

        with self._config.change_default() as conf:
            conf["lua_script_path"] = str(greyboxing_path)
            conf["backends"] = {}
            conf["bind_address"] = self.address

        self._config.write_default()

    async def terminate(self):
        if self._terminated:
            return
        self._terminated = True
        await self.stop()
        self._config.terminate()

    def __del__(self):
        if not self._terminated:
            raise Exception("service was not terminated")

    async def stop(self, *, locked: bool = False) -> None:
        if not locked:
            async with self._restart_lock:
                return await self.stop(locked=True)

        if self._process is None:
            return

        # Fast-path: check if process has already exited
        if self._process.returncode is not None:
            self._process = None
            return

        print(f"[LLMModule] Stopping process (PID: {self._process.pid})")

        try:
            # Try graceful shutdown with SIGINT
            with contextlib.suppress(ProcessLookupError):
                self._process.send_signal(signal.SIGINT)

            try:
                # Wait for process to terminate with a timeout
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
                print("[LLMModule] Process terminated gracefully")
            except asyncio.TimeoutError:
                print(
                    "[LLMModule] Process didn't terminate with SIGINT, trying forceful termination"
                )
                # If SIGINT didn't work, use kill() for cross-platform compatibility
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=2.0)
                        print("[LLMModule] Process terminated forcefully")
                    except asyncio.TimeoutError:
                        print(
                            "[LLMModule] Process termination failed, continuing anyway"
                        )
        finally:
            # Ensure process handle is cleared even if exception occurs
            self._process = None

    async def restart(self) -> None:
        async with self._restart_lock:
            await self._restart_locked()

    async def _restart_locked(self) -> None:
        await self.stop(locked=True)

        genvm_bin = os.getenv("GENVM_BIN")
        if genvm_bin is None:
            raise RuntimeError("GENVM_BIN env var is not set")

        exe_path = Path(genvm_bin).joinpath("genvm-modules")

        debug_enabled = os.getenv("GENVM_LLM_DEBUG") == "1"
        stream_target = None if debug_enabled else asyncio.subprocess.DEVNULL

        self._process = await asyncio.subprocess.create_subprocess_exec(
            exe_path,
            "llm",
            "--config",
            self._config.new_path,
            "--allow-empty-backends",
            "--die-with-parent",
            stdin=None,
            stdout=stream_target,
            stderr=stream_target,
        )

    async def verify_for_read(self) -> None:
        async with self._restart_lock:
            if self._process is None:
                await self._restart_locked()
            elif self._process.returncode is not None:
                print(
                    f"LLM process died with code {self._process.returncode}, restarting..."
                )
                await self._restart_locked()

    async def change_config(self, new_providers: list[SimulatorProvider]):
        await self.stop()

        with self._config.change() as conf:
            for provider in new_providers:
                conf["backends"][provider.id] = {
                    "host": provider.url,
                    "provider": provider.plugin,
                    "key": "${ENV[" + provider.key_env + "]}",
                    "models": {
                        provider.model: {
                            "supports_json": True,
                            "supports_image": False,
                        }
                    },
                }

        await self.restart()

    async def provider_available(
        self, model: str, url: str | None, plugin: str, key_env: str
    ) -> bool:
        if url is None:
            return False

        if plugin == "custom":
            return await self.call_custom_model(model, url, key_env)

        genvm_bin = os.getenv("GENVM_BIN")
        if not genvm_bin:
            logger.error(
                "GENVM_BIN env var is not set; cannot validate provider %s", model
            )
            return False

        exe_path = Path(genvm_bin).joinpath("genvm-modules")

        try:
            proc = await asyncio.subprocess.create_subprocess_exec(
                exe_path,
                "llm-check",
                "--provider",
                plugin,
                "--host",
                url,
                "--model",
                model,
                "--key",
                "${ENV[" + key_env + "]}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            stdout, _ = await proc.communicate()
            return_code = await proc.wait()

            stdout_text = stdout.decode("utf-8", errors="replace")

            if return_code != 0:
                error_info = extract_error_message(stdout_text)
                logger.warning(
                    "Provider not available model=%s error=%s",
                    model,
                    error_info,
                )

            return return_code == 0

        except Exception as e:
            print(
                f"ERROR: Wrong input provider_available {model=}, {url=}, {plugin=}, {key_env=}, {e=}"
            )
            return False

    async def call_custom_model(self, model: str, url: str, key_env: str) -> bool:
        """
        Call a custom model to check if it is available.
        """
        try:
            prompt = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": 'Respond with two letters "ok" (without quotes) and only this word, lowercase',
                    }
                ],
            }

            api_key = os.environ.get(key_env)
            if not api_key:
                print(f"ERROR: missing API key for {key_env}")
                return False

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session, session.post(
                url, json=prompt, headers={"Authorization": f"Bearer {api_key}"}
            ) as response:
                if response.status != 200:
                    print(
                        f"ERROR: Custom model check failed with status {response.status}"
                    )
                    return False

                response_data = await response.json()
                try:
                    result = response_data["choices"][0]["message"]["content"]
                    if isinstance(result, str) and result.strip().lower() == "ok":
                        return True
                    elif (
                        isinstance(result, dict)
                        and "result" in result
                        and result["result"].strip().lower() == "ok"
                    ):
                        return True

                    print(
                        f"ERROR: Custom model check failed: got '{result}' instead of 'ok'"
                    )
                    return False

                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    print(f"ERROR: Invalid response format: {e}")
                    return False

        except Exception as e:
            print(f"ERROR: Custom model check failed with error: {e}")
            return False
