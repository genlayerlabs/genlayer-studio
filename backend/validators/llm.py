__all__ = (
    "LLMModule",
    "SimulatorProvider",
)

import asyncio
import signal
import os
import sys
import dataclasses
import aiohttp
from pathlib import Path
import json
import contextlib

from dotenv import load_dotenv

load_dotenv()

import backend.validators.base as base


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

        greyboxing_path = Path(__file__).parent.joinpath("greyboxing.lua")

        self._config = base.ChangedConfigFile(base.LLM_CONFIG_PATH)

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

    async def stop(self):
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

    async def restart(self):
        await self.stop()

        self._process = await asyncio.subprocess.create_subprocess_exec(
            base.MODULES_BINARY,
            "llm",
            "--config",
            self._config.new_path,
            "--allow-empty-backends",
            "--die-with-parent",
            stdin=None,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

    async def verify_for_read(self):
        if self._process is None:
            raise Exception("process is not started")
        if self._process.returncode is not None:
            raise Exception(f"process is dead {self._process.returncode}")

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

        try:
            proc = await asyncio.subprocess.create_subprocess_exec(
                base.MODULES_BINARY,
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

            stdout = stdout.decode("utf-8")

            if return_code != 0:
                print(f"provider not available model={model} stdout={stdout!r}")

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
