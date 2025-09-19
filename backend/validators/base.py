import os
import io
import typing
import contextlib
import tempfile

from pathlib import Path
from copy import deepcopy

import backend.node.genvm.config as genvm_config

_DYN_ATTRS = set(
    [
        "GENVM_BINARY",
        "MODULES_BINARY",
        "GENVM_CONFIG_PATH",
        "LLM_CONFIG_PATH",
        "WEB_CONFIG_PATH",
    ]
)


def __getattr__(name: str) -> typing.Any:
    if name not in _DYN_ATTRS:
        raise AttributeError(f"module {__name__} has no attribute {name}")

    GENVM_BINARY = genvm_config._find_exe("genvm")
    MODULES_BINARY = genvm_config._find_exe("genvm-modules", env_name="GENVMROOT")

    GENVM_CONFIG_PATH = GENVM_BINARY.parent.parent.joinpath("config", "genvm.yaml")
    LLM_CONFIG_PATH = MODULES_BINARY.parent.parent.joinpath(
        "config", "genvm-module-llm.yaml"
    )
    WEB_CONFIG_PATH = MODULES_BINARY.parent.parent.joinpath(
        "config", "genvm-module-web.yaml"
    )

    for k in _DYN_ATTRS:
        globals()[k] = locals()[k]

    return globals()[name]


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

    def __init__(self, base: Path):
        import yaml

        self._default_conf = typing.cast(dict, yaml.safe_load(base.read_text()))

        fd, name = tempfile.mkstemp("-" + base.name, "studio-")
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
