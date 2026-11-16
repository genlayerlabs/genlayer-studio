import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

GENVM_NETWORK = "studio"
# Resolved from the source tree, so docker images, bind mounts and local runs agree
CHAINFORKS_PATH = Path(__file__).resolve().with_name("chainforks.json")

_ALWAYS = datetime.datetime.min.replace(tzinfo=datetime.UTC)
# genvm-manager's activation pattern, with fractions capped at datetime's microseconds
_TIMESTAMP = re.compile(
    r"[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])"
)


def manager_args() -> list[str]:
    """Flags that make genvm-manager serve and check Studio's schedule."""
    return [
        "--chainforks-path",
        str(CHAINFORKS_PATH),
        "--default-network",
        GENVM_NETWORK,
    ]


def check_install_argv(genvm_root: Path) -> list[str]:
    return [
        str(genvm_root.joinpath("bin", "genvm-modules")),
        "manager",
        "check-install",
        *manager_args(),
    ]


def fork_activation(
    fork: str, *, path: Path = CHAINFORKS_PATH, network: str = GENVM_NETWORK
) -> datetime.datetime:
    """When `network` activates `fork`; `always` is the minimal timestamp.

    Raises ValueError when the fork is missing, `never`, or not a timestamp:
    callers rely on the fork being reachable.
    """
    try:
        schedule = json.loads(path.read_text())
        activation = schedule["networks"][network]["forks"][fork]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(
            f"{path}: cannot read {fork!r} activation on network {network!r}"
        ) from e
    if activation == "always":
        return _ALWAYS
    if not isinstance(activation, str) or activation == "never":
        raise ValueError(
            f"{path}: network {network!r} must activate {fork!r}, got {activation!r}"
        )
    malformed = ValueError(
        f"{path}: {fork!r} activation {activation!r} is not an RFC 3339 timestamp"
    )
    if not _TIMESTAMP.fullmatch(activation):
        raise malformed
    try:
        return datetime.datetime.fromisoformat(activation)
    except ValueError as e:
        raise malformed from e


if __name__ == "__main__":
    sys.exit(
        subprocess.run(check_install_argv(Path(os.environ["GENVMROOT"]))).returncode
    )
