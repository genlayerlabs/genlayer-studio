import asyncio
import signal
import os
import sys

from pathlib import Path

from .base import ChangedConfigFile


class WebModule:
    _process: asyncio.subprocess.Process | None

    def __init__(self):
        self.address = "127.0.0.1:3031"

        self._terminated = False

        self._process = None

        self._config = ChangedConfigFile("genvm-module-web.yaml")

        web_script_path = Path(__file__).parent.joinpath("web.lua")

        with self._config.change_default() as conf:
            conf["webdriver_host"] = (
                f"{os.getenv('WEBDRIVERPROTOCOL', 'http')}://{os.environ['WEBDRIVERHOST']}:{os.environ['WEBDRIVERPORT']}"
            )
            conf["bind_address"] = self.address
            conf["lua_script_path"] = str(web_script_path)

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

    async def restart(self):
        await self.stop()

        exe_path = Path(os.environ["GENVM_BIN"]).joinpath("genvm-modules")

        self._process = await asyncio.subprocess.create_subprocess_exec(
            exe_path,
            "web",
            "--config",
            self._config.new_path,
            "--die-with-parent",
            stdin=None,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

    async def stop(self):
        if self._process is not None:
            print(f"[WebModule] Stopping process (PID: {self._process.pid})")
            try:
                self._process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            
            try:
                # Wait for process to terminate with a timeout
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
                print(f"[WebModule] Process terminated gracefully")
            except asyncio.TimeoutError:
                print(f"[WebModule] Process didn't terminate with SIGINT, trying SIGKILL")
                # If SIGINT didn't work, try SIGKILL
                try:
                    self._process.send_signal(signal.SIGKILL)
                    await asyncio.wait_for(self._process.wait(), timeout=2.0)
                    print(f"[WebModule] Process terminated with SIGKILL")
                except (asyncio.TimeoutError, ProcessLookupError):
                    print(f"[WebModule] Process termination failed, continuing anyway")
                    # If still hanging, just give up and set to None
                    pass
            
            self._process = None

    async def verify_for_read(self):
        if self._process is None:
            raise Exception("process is not started")
        if self._process.returncode is not None:
            raise Exception(f"process is dead {self._process.returncode}")
