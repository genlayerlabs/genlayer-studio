import asyncio
import contextlib
import logging
import os
import signal

from pathlib import Path

from dotenv import load_dotenv

import backend.validators.base as base


load_dotenv()

logger = logging.getLogger(__name__)


class WebModule:
    _process: asyncio.subprocess.Process | None

    def __init__(self):
        self.address = "127.0.0.1:3031"

        self._terminated = False

        self._process = None

        self._config = base.ChangedConfigFile(base.WEB_CONFIG_PATH)

        web_script_path = Path(__file__).parent.joinpath("web.lua")

        # Resolve webdriver endpoint from environment (defaults align with docker-compose)
        webdriver_host = os.getenv("WEBDRIVERHOST", "webdriver")
        webdriver_port = os.getenv("WEBDRIVERPORT", "5001")
        webdriver_protocol = os.getenv("WEBDRIVERPROTOCOL") or os.getenv(
            "RPCPROTOCOL", "http"
        )

        with self._config.change_default() as conf:
            conf["webdriver_host"] = (
                f"{webdriver_protocol}://{webdriver_host}:{webdriver_port}"
            )
            conf["bind_address"] = self.address
            conf["lua_script_path"] = str(web_script_path)

        self._config.write_default()

        # Keep reference to the GenVM binary root for consistency with LLM module
        self._genvm_bin = base.GENVM_BINARY

    async def terminate(self) -> None:
        if self._terminated:
            return
        self._terminated = True
        await self.stop()
        self._config.terminate()

    def __del__(self) -> None:
        if not self._terminated:
            try:
                logger.warning("WebModule was not terminated")
            except Exception:  # pragma: no cover - best effort cleanup
                pass

    async def restart(self) -> None:
        await self.stop()

        debug_enabled = os.getenv("GENVM_WEB_DEBUG") == "1"
        stream_target = None if debug_enabled else asyncio.subprocess.DEVNULL

        self._process = await asyncio.subprocess.create_subprocess_exec(
            base.MODULES_BINARY,
            "web",
            "--config",
            self._config.new_path,
            "--die-with-parent",
            stdin=None,
            stdout=stream_target,
            stderr=stream_target,
        )

    async def stop(self) -> None:
        if self._process is None:
            return

        # Fast-path: check if process has already exited
        if self._process.returncode is not None:
            self._process = None
            return

        logger.info("Stopping WebModule process (PID: %s)", self._process.pid)

        try:
            # Try graceful shutdown with SIGINT
            with contextlib.suppress(ProcessLookupError):
                self._process.send_signal(signal.SIGINT)

            try:
                # Wait for process to terminate with a timeout
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
                logger.info("WebModule process terminated gracefully")
            except asyncio.TimeoutError:
                logger.warning(
                    "WebModule process did not terminate with SIGINT; attempting kill"
                )
                # If SIGINT didn't work, use kill() for cross-platform compatibility
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=2.0)
                        logger.info("WebModule process terminated forcefully")
                    except asyncio.TimeoutError:
                        logger.warning(
                            "WebModule process termination forced kill timed out; continuing"
                        )
        finally:
            # Ensure process handle is cleared even if exception occurs
            self._process = None

    async def verify_for_read(self) -> None:
        if self._process is None:
            # Start the process if it hasn't been started
            await self.restart()
        elif self._process.returncode is not None:
            # Restart the process if it's dead
            logger.warning(
                "WebModule process exited with code %s; restarting",
                self._process.returncode,
            )
            await self.restart()
