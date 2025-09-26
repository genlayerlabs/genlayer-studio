import asyncio
import contextlib
import logging
import os
import signal

from pathlib import Path

from .base import ChangedConfigFile


logger = logging.getLogger(__name__)


class WebModule:
    _process: asyncio.subprocess.Process | None

    def __init__(self):
        self.address = "127.0.0.1:3031"

        self._terminated = False

        self._process = None

        try:
            genvm_bin = os.environ["GENVM_BIN"]
        except KeyError as exc:  # pragma: no cover - startup validation
            raise RuntimeError("GENVM_BIN environment variable must be set") from exc

        protocol = os.getenv("WEBDRIVERPROTOCOL", "http")
        try:
            webdriver_host = os.environ["WEBDRIVERHOST"]
            webdriver_port = os.environ["WEBDRIVERPORT"]
        except KeyError as exc:  # pragma: no cover - startup validation
            missing = exc.args[0]
            raise RuntimeError(f"{missing} environment variable must be set") from exc

        self._config = ChangedConfigFile("genvm-module-web.yaml")

        web_script_path = Path(__file__).parent.joinpath("web.lua")

        with self._config.change_default() as conf:
            conf["webdriver_host"] = f"{protocol}://{webdriver_host}:{webdriver_port}"
            conf["bind_address"] = self.address
            conf["lua_script_path"] = str(web_script_path)

        self._config.write_default()

        self._genvm_bin = genvm_bin

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

        exe_path = Path(self._genvm_bin).joinpath("genvm-modules")

        self._process = await asyncio.subprocess.create_subprocess_exec(
            exe_path,
            "web",
            "--config",
            self._config.new_path,
            "--die-with-parent",
            stdin=None,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
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
