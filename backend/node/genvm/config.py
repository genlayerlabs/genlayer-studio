import os
from pathlib import Path

# TODO(kp2pml30): this needs to be rewritten


def _check_one(check: Path) -> bool:
    try:
        return check.exists() and check.is_file()
    except:
        return False


def _find_exe(name: str, *, env_name: str | None = None) -> Path:
    if env_name is None:
        env_name = name.upper()
    checked = []
    for env_var in [env_name, f"{env_name}PATH", f"{env_name}BIN", f"{env_name}ROOT"]:
        var = os.getenv(env_var)
        if var is None:
            continue
        subpaths = [["bin"], ["executor", os.getenv("GENVM_TAG", "."), "bin"]]
        for subpath_check in subpaths:
            for prefix in range(len(subpath_check) + 1):
                sub = subpath_check[:prefix]
                check = Path(var).joinpath(*sub, name)
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
