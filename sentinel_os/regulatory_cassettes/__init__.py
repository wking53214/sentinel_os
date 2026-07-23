"""Regulatory lens implementations.

DELIBERATELY a separate directory from cassettes/ (the domain
cassettes): the domain CassetteLoader auto-discovers cassettes/ by
globbing *_cassette.py, and a regulatory lens must never be picked up
by that path -- a lens is not operational policy and would (correctly)
fail domain validation, which under fail_on_invalid=True would take
down every harness construction. Separate directory, separate naming
convention (no _cassette suffix), separate registry
(regulatory_cassette_interface.RegulatoryCassetteRegistry).
"""
