"""An audit run must not append to the chain it checks.

No database needed: the boot-time marker step is exercised on a bare ledger
object with the append recorded instead of executed.
"""
from governance import ledger_postgres as lp


def _bare_ledger(append_policy_marker):
    ledger = object.__new__(lp.PostgreSQLLedger)
    ledger._append_policy_marker = append_policy_marker
    calls = []
    ledger.record_attestation_policy = lambda fp, *a, **k: calls.append(fp)
    return ledger, calls


def _force_enforced(monkeypatch):
    monkeypatch.setattr(lp, "enforcement_required", lambda: True)
    monkeypatch.setattr(lp, "attestation_key", lambda: b"k" * 32)


def test_default_still_appends_marker(monkeypatch):
    _force_enforced(monkeypatch)
    ledger, calls = _bare_ledger(True)
    ledger._ensure_attestation_policy_marker()
    assert len(calls) == 1


def test_read_only_open_appends_nothing(monkeypatch):
    _force_enforced(monkeypatch)
    ledger, calls = _bare_ledger(False)
    ledger._ensure_attestation_policy_marker()
    assert calls == []


def test_verify_ledger_script_opts_out():
    import pathlib
    src = pathlib.Path(__file__).parent.joinpath(
        "scripts", "verify_ledger.py").read_text()
    assert "append_policy_marker=False" in src
