"""Reference contract lens -- a standard data-processing agreement.

This is the contract-side equivalent of regulatory_cassettes/cfpb_reg_b.py:
a worked reference implementation showing how a signed agreement is
expressed as structured terms, not a contract anyone has actually
signed. The counterparty_id here is deliberately obviously fictional.

The terms below are the four types contract_cassette declares, one
each, in the shape a real DPA tends to take:

  * personal data deleted from active systems within 90 days, with a
    365-day backup carve-out (see contract_cassette's note on why that
    parameter exists rather than forcing every real agreement to be
    authored as a lie);
  * no egress at all for model training, as an outright prohibition
    rather than an approval gate -- and note what this can and cannot
    prove: the egress log shows what left and to whom, never what a
    recipient did with it afterwards. Proving data was not used to
    train a model downstream is not provable from inside this
    boundary. The term is still worth declaring, because an egress TO
    a training purpose is catchable even though downstream use is not;
  * subcontractor sharing permitted only against a live, unrevoked
    approval;
  * purpose restricted to the two things the agreement was for.
"""
from typing import Tuple

from contract_cassette import (
    TERM_EGRESS_PROHIBITED,
    TERM_EGRESS_REQUIRES_APPROVAL,
    TERM_PURPOSE_RESTRICTION,
    TERM_RETENTION_MAX_DAYS,
    ContractCassette,
    ContractTerm,
    RECIPIENT_CLASS_SUBCONTRACTOR,
)


class ReferenceDPAContract(ContractCassette):
    """Reference data-processing agreement for a fictional counterparty."""

    COUNTERPARTY_ID = "example-counterparty"
    CONTRACT_REFERENCE = "DPA-REFERENCE-0001 rev A (illustrative, unsigned)"
    CONTRACT_VERSION = "1.0.0"

    RETENTION_MAX_DAYS = 90
    BACKUP_MAX_DAYS = 365

    def get_counterparty_id(self) -> str:
        return self.COUNTERPARTY_ID

    def get_contract_reference(self) -> str:
        return self.CONTRACT_REFERENCE

    def get_contract_version(self) -> str:
        return self.CONTRACT_VERSION

    def get_terms(self) -> Tuple[ContractTerm, ...]:
        return (
            ContractTerm(TERM_RETENTION_MAX_DAYS, {
                "max_days": self.RETENTION_MAX_DAYS,
                "backup_max_days": self.BACKUP_MAX_DAYS,
            }),
            ContractTerm(TERM_EGRESS_PROHIBITED, {
                "purpose": "model_training",
            }),
            ContractTerm(TERM_EGRESS_REQUIRES_APPROVAL, {
                "recipient_class": RECIPIENT_CLASS_SUBCONTRACTOR,
            }),
            ContractTerm(TERM_PURPOSE_RESTRICTION, {
                "permitted_purposes": ["fraud_screening", "statement_generation"],
            }),
        )
