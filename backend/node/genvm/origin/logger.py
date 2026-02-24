import abc
import json
import sys
import traceback


class Logger(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def log(self, level: str, msg: str, **kwargs) -> None: ...

    def trace(self, msg: str, **kwargs) -> None:
        self.log("trace", msg, **kwargs)

    def debug(self, msg: str, **kwargs) -> None:
        self.log("debug", msg, **kwargs)

    def info(self, msg: str, **kwargs) -> None:
        self.log("info", msg, **kwargs)

    def warning(self, msg: str, **kwargs) -> None:
        self.log("warning", msg, **kwargs)

    def error(self, msg: str, **kwargs) -> None:
        self.log("error", msg, **kwargs)

    def with_keys(self, keys: dict) -> "Logger":
        return _WithKeysLogger(self, keys)


import collections.abc


def _log_unwrap(x, seen: set[int]):
    if isinstance(x, bytes):
        return x.hex()
    if x is None:
        return None
    if isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, BaseException):
        tb = traceback.format_exception(x)
        return _log_unwrap(
            {
                "message": x.args[0] if len(x.args) == 1 else x.args,
                "type": x.__class__.__name__,
                "notes": getattr(x, "__notes__", []),
                "traceback": tb,
            },
            seen,
        )
    x_id = id(x)
    if x_id in seen:
        return f"<{x_id}>"
    seen.add(x_id)
    if isinstance(x, dict):
        return {k: _log_unwrap(v, seen) for k, v in x.items()}
    if isinstance(x, collections.abc.Sequence):
        return [_log_unwrap(v, seen) for v in x]
    seen.remove(x_id)
    return repr(x)


class _WithKeysLogger(Logger):
    keys: dict

    def __init__(self, parent: Logger, keys: dict):
        if isinstance(parent, _WithKeysLogger):
            self.keys = {**parent.keys, **keys}
            self.parent = parent.parent
        else:
            self.keys = keys
            self.parent = parent

    def log(self, level: str, msg: str, **kwargs) -> None:
        self.parent.log(level, msg, **self.keys, **kwargs)

    def with_keys(self, keys: dict) -> Logger:
        return _WithKeysLogger(self.parent, {**self.keys, **keys})


class NoLogger(Logger):
    def log(self, level: str, msg: str, **kwargs) -> None:
        pass

    def with_keys(self, keys: dict) -> "Logger":
        return self


_level_to_num = {
    "trace": 10,
    "debug": 20,
    "info": 30,
    "warning": 40,
    "error": 50,
}


class StderrLogger(Logger):
    def __init__(self, min_level: str = "info"):
        self.min_level = _level_to_num[min_level]

    def log(self, level: str, msg: str, **kwargs) -> None:
        if _level_to_num[level] < self.min_level:
            return
        json.dump(
            _log_unwrap(
                {
                    "message": msg,
                    "level": level,
                    **kwargs,
                },
                set(),
            ),
            sys.stderr,
        )
        sys.stderr.write("\n")
        sys.stderr.flush()
