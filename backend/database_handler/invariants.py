from sqlalchemy.orm import Session
from sqlalchemy import text


def get_total_balances(session: Session) -> int:
    result = session.execute(
        text("SELECT COALESCE(SUM(balance), 0) FROM current_state")
    ).scalar()
    return int(result)


def get_in_transit_value(session: Session) -> int:
    result = session.execute(
        text(
            """
            SELECT COALESCE(SUM(value), 0) FROM transactions
            WHERE triggered_by_hash IS NOT NULL
            AND value > 0
            AND status IN ('PENDING', 'ACTIVATED')
            """
        )
    ).scalar()
    return int(result)


def get_total_minted(session: Session) -> int:
    result = session.execute(
        text(
            """
            SELECT COALESCE(SUM(value), 0) FROM transactions
            WHERE from_address IS NULL AND type = 0
            """
        )
    ).scalar()
    return int(result)


def verify_conservation(session: Session) -> tuple[bool, dict]:
    total_balances = get_total_balances(session)
    in_transit = get_in_transit_value(session)
    total_minted = get_total_minted(session)

    system_total = total_balances + in_transit
    is_valid = system_total == total_minted

    return is_valid, {
        "total_balances": total_balances,
        "in_transit_value": in_transit,
        "total_minted": total_minted,
        "system_total": system_total,
        "delta": system_total - total_minted,
    }
