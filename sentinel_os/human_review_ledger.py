"""Lazy adapter for human-review API access to the governed-decision ledger."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_human_review_ledger():
    """Return the process-wide PostgreSQL ledger used by review routes.

    Construction retains PostgreSQLLedger's startup checks for restricted
    runtime credentials, immutable-table triggers, and attestation settings.
    The owner password comes from POSTGRES_PASSWORD; there is no default.
    """
    import os

    from governance.ledger_postgres import PostgreSQLLedger

    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        # No default: a guessable fallback would let a misconfigured
        # deployment connect with a well-known credential.
        raise RuntimeError("POSTGRES_PASSWORD is not set")
    return PostgreSQLLedger(password=password)
