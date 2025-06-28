import os
import io
import typing
import contextlib
import tempfile

from pathlib import Path
from copy import deepcopy

GENVM_BIN_DIR = Path(os.environ["GENVM_BIN"])
GENVM_DEFAULT_CONFIG_DIR = Path(os.environ["GENVM_BIN"]).parent.joinpath("config")
GENVM_SCRIPT_DIR = Path(os.environ["GENVM_BIN"]).parent.joinpath("scripts")


class _Stream:
    __slots__ = ("f",)

    def __init__(self, f: io.FileIO):
        self.f = f

    def flush(self):
        self.f.flush()

    def write(self, x):
        if isinstance(x, str):
            self.f.write(x.encode("utf-8"))
        else:
            self.f.write(x)


class ChangedConfigFile:
    new_path: Path

    _file: io.FileIO
    _default_conf: dict

    def __init__(self, base: str):
        import yaml

        self._default_conf = typing.cast(
            dict, yaml.safe_load(GENVM_DEFAULT_CONFIG_DIR.joinpath(base).read_text())
        )

        fd, name = tempfile.mkstemp("-" + base, "studio-")
        self.new_path = Path(name)

        self._file = io.FileIO(fd, "w")
        self._stream = _Stream(self._file)

    def terminate(self):
        self._file.close()
        self.new_path.unlink(True)

    @contextlib.contextmanager
    def change_default(self):
        yield self._default_conf

    def write_default(self):
        import yaml

        self._file.seek(0, io.SEEK_SET)
        yaml.dump(self._default_conf, self._stream)

        self._file.truncate()

        self._file.flush()
        os.fsync(self._file.fileno())

    @contextlib.contextmanager
    def change(self):
        data = deepcopy(self._default_conf)
        yield data

        import yaml

        self._file.seek(0, io.SEEK_SET)
        yaml.dump(data, self._stream)

        self._file.truncate()

        self._file.flush()
        os.fsync(self._file.fileno())
