"""Lazy adapter for human-review API access to the governed-decision ledger."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_human_review_ledger():
    """Return the process-wide PostgreSQL ledger used by review routes.

    Construction retains PostgreSQLLedger's startup checks for restricted
    runtime credentials, immutable-table triggers, and attestation settings.
    """
    from governance.ledger_postgres import PostgreSQLLedger

    return PostgreSQLLedger()
