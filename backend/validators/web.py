import asyncio
import signal
import os
import sys
import contextlib

from pathlib import Path

import backend.validators.base as base


class WebModule:
    _process: asyncio.subprocess.Process | None

    def __init__(self):
        self.address = "127.0.0.1:3031"

        self._terminated = False

        self._process = None

        self._config = base.ChangedConfigFile(base.WEB_CONFIG_PATH)

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

        self._process = await asyncio.subprocess.create_subprocess_exec(
            base.MODULES_BINARY,
            "web",
            "--config",
            self._config.new_path,
            "--die-with-parent",
            stdin=None,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

    async def stop(self):
        if self._process is None:
            return

        # Fast-path: check if process has already exited
        if self._process.returncode is not None:
            self._process = None
            return

        print(f"[WebModule] Stopping process (PID: {self._process.pid})")

        try:
            # Try graceful shutdown with SIGINT
            with contextlib.suppress(ProcessLookupError):
                self._process.send_signal(signal.SIGINT)

            try:
                # Wait for process to terminate with a timeout
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
                print("[WebModule] Process terminated gracefully")
            except asyncio.TimeoutError:
                print(
                    "[WebModule] Process didn't terminate with SIGINT, trying forceful termination"
                )
                # If SIGINT didn't work, use kill() for cross-platform compatibility
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=2.0)
                        print("[WebModule] Process terminated forcefully")
                    except asyncio.TimeoutError:
                        print(
                            "[WebModule] Process termination failed, continuing anyway"
                        )
        finally:
            # Ensure process handle is cleared even if exception occurs
            self._process = None

    async def verify_for_read(self):
        if self._process is None:
            raise Exception("process is not started")
        if self._process.returncode is not None:
            raise Exception(f"process is dead {self._process.returncode}")
