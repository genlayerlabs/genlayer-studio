import re
from pathlib import Path

from backend.database_handler.models import TransactionStatus

DOC = Path(__file__).resolve().parents[2] / "docs" / "spec-alignment.md"


def test_every_transaction_status_is_documented():
    documented = set(re.findall(r"`([A-Z_]+)`", DOC.read_text()))
    missing = {s.value for s in TransactionStatus} - documented
    assert not missing, f"docs/spec-alignment.md is missing statuses: {missing}"
