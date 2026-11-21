from pathlib import Path

from backend.database_handler.models import TransactionStatus

DOC = Path(__file__).resolve().parents[2] / "docs" / "spec-alignment.md"

SPEC_STATUSES = [
    "Uninitialized",
    "Pending",
    "Proposing",
    "Committing",
    "LeaderRevealing",
    "Revealing",
    "Accepted",
    "Undetermined",
    "LeaderTimeout",
    "ValidatorsTimeout",
    "ReadyToFinalize",
    "Finalized",
    "Canceled",
    "AppealCommitting",
    "AppealRevealing",
]


def _status_table_rows():
    """Return (studio_cell, spec_cell) for each row of the status table."""
    rows = []
    in_table = False
    for line in DOC.read_text().splitlines():
        if line.startswith("| Studio status"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if set(cells[0]) <= {"-", " "}:
            continue
        rows.append((cells[0], cells[1]))
    return rows


def test_every_stored_status_has_a_studio_column_entry():
    studio_cells = " ".join(studio for studio, _ in _status_table_rows())
    missing = [s.value for s in TransactionStatus if f"`{s.value}`" not in studio_cells]
    assert not missing, f"Studio column is missing statuses: {missing}"


def test_every_spec_status_has_a_row():
    spec_cells = " ".join(spec for _, spec in _status_table_rows())
    missing = [s for s in SPEC_STATUSES if f"`{s}`" not in spec_cells]
    assert not missing, f"Spec column is missing statuses: {missing}"
