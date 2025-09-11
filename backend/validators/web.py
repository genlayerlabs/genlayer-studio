import asyncio
import signal
import os
import sys
import contextlib

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

        # Validate required environment variables
        required_env_vars = ["GENVM_BIN", "WEBDRIVERHOST", "WEBDRIVERPORT"]
        missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
        if missing_vars:
            error_msg = f"[WebModule] Missing required environment variables: {', '.join(missing_vars)}"
            print(error_msg)
            raise RuntimeError(error_msg)

        exe_path = Path(os.environ["GENVM_BIN"]).joinpath("genvm-modules")
        if not exe_path.exists():
            error_msg = f"[WebModule] genvm-modules binary not found at: {exe_path}"
            print(error_msg)
            raise RuntimeError(error_msg)

        print(f"[WebModule] Starting web module with binary: {exe_path}")
        print(f"[WebModule] Config: {self._config.new_path}")
        print(f"[WebModule] WebDriver: {os.getenv('WEBDRIVERPROTOCOL', 'http')}://{os.environ['WEBDRIVERHOST']}:{os.environ['WEBDRIVERPORT']}")

        self._process = await asyncio.subprocess.create_subprocess_exec(
            exe_path,
            "web",
            "--config",
            self._config.new_path,
            "--die-with-parent",
            stdin=None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Start background task to monitor process output
        asyncio.create_task(self._monitor_process())

    async def _monitor_process(self):
        """Monitor process output for debugging."""
        if self._process is None:
            return
            
        try:
            # Read stdout
            if self._process.stdout:
                asyncio.create_task(self._read_stream(self._process.stdout, "stdout"))
            
            # Read stderr
            if self._process.stderr:
                asyncio.create_task(self._read_stream(self._process.stderr, "stderr"))
                
            # Wait for process to exit
            return_code = await self._process.wait()
            if return_code != 0:
                print(f"[WebModule] Process exited with code {return_code}")
        except Exception as e:
            print(f"[WebModule] Error monitoring process: {e}")
    
    async def _read_stream(self, stream, stream_name):
        """Read and log output from a stream."""
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded_line = line.decode('utf-8').strip()
                if decoded_line:
                    print(f"[WebModule] {stream_name}: {decoded_line}")
        except Exception as e:
            print(f"[WebModule] Error reading {stream_name}: {e}")

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
            # Start the process if it hasn't been started
            await self.restart()
        elif self._process.returncode is not None:
            # Restart the process if it's dead
            print(f"Web module process died with code {self._process.returncode}, restarting...")
            await self.restart()
