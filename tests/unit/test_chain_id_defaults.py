import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _decimal_capture(path: str, pattern: str) -> int:
    source = (REPOSITORY_ROOT / path).read_text(encoding="utf-8")
    match = re.search(pattern, source, re.MULTILINE)
    assert match is not None, f"could not find the chain ID default in {path}"
    return int(match.group(1))


def test_tracked_local_chain_id_defaults_stay_aligned():
    defaults = {
        "environment": _decimal_capture(
            ".env.example", r"^GENLAYER_CHAIN_ID=[\'\"]?(\d+)[\'\"]?$"
        ),
        "backend": _decimal_capture(
            "backend/node/base.py",
            r'os\.getenv\("HARDHAT_CHAIN_ID",\s*"(\d+)"\)',
        ),
        "hardhat": _decimal_capture(
            "hardhat/hardhat.config.js",
            r'readDotenvVariable\("HARDHAT_CHAIN_ID"\)\s*\?\?\s*"(\d+)"',
        ),
    }

    assert defaults == {
        "environment": 61127,
        "backend": 61127,
        "hardhat": 61127,
    }
