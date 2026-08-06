"""Contract lens implementations -- one module per signed contract.

DELIBERATELY a separate directory from cassettes/ (domain policy) and
from regulatory_cassettes/ (agency rules), for the same reason the
latter is separate: the domain CassetteLoader auto-discovers
cassettes/ by globbing *_cassette.py, and a contract lens must never
be picked up by that path. A contract is not operational policy, would
correctly fail domain validation, and under fail_on_invalid=True would
take down every harness construction.

Separate directory, separate naming convention (no _cassette suffix on
the file), separate registry
(contract_cassette.ContractCassetteRegistry), separate reserved
identity slot ("contract:<counterparty_id>:<version>").

The files here contain the terms of agreements real counterparties
signed. Treat them as contract text: a change to a term is a change to
what the operator is claiming to be bound by, and the content-hash
binding is what turns that claim into something a counterparty can
check against their own copy.
"""
