__all__ = (
    "LLMModule",
    "SimulatorProvider",
)

import asyncio
import signal
import os
import sys
import dataclasses

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .base import *


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

    async def stop(self):
        if self._process is not None:
            try:
                self._process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            await self._process.wait()
            self._process = None

    async def restart(self):
        await self.stop()

        exe_path = Path(os.environ["GENVM_BIN"]).joinpath("genvm-modules")

        self._process = await asyncio.subprocess.create_subprocess_exec(
            exe_path,
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
        self, model: str, url: str, plugin: str, key_env: str
    ) -> bool:
        if plugin == "custom":
            return True

        exe_path = Path(os.environ["GENVM_BIN"]).joinpath("genvm-modules")

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

        stdout = stdout.decode("utf-8")

        if return_code != 0:
            print(f"provider not available model={model} stdout={stdout!r}")

        return return_code == 0
