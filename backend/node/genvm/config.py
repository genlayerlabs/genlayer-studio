import os
from pathlib import Path


def _check_one(check: Path) -> bool:
    try:
        return check.exists() and check.is_file()
    except:
        return False


def _find_exe(name: str) -> Path:
    checked = []
    for env_var in [f"{name.upper()}PATH", f"{name.upper()}_BIN"]:
        var = os.getenv(env_var)
        if var is None:
            continue
        for check in [Path(var), Path(var).joinpath(name)]:
            checked.append(check)
            if _check_one(check):
                return check
    for p in os.getenv("PATH", "").split(":"):
        check = Path(p).joinpath(name)
        checked.append(check)
        if _check_one(check):
            return check
    raise Exception(f"Can't find executable {name}, searched at {checked}")


_found_at: Path | None = None


def get_genvm_path() -> Path:
    global _found_at
    if _found_at is None:
        _found_at = _find_exe("genvm")

    return _found_at
