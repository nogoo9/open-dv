"""Canonical catalog of known polysemic business terms for financial ontologies.

Conforms to docs/19-ontology-studio-architecture.md (§5.4) and
docs/20-ontology-studio-spec.md.
"""

from __future__ import annotations

KNOWN_POLYSEMIC_DEFINITIONS: dict[str, list[dict[str, str]]] = {
    "account": [
        {
            "label": "General Ledger Account",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/"
                "ClientsAndAccounts/LedgerAccount"
            ),
            "rationale": (
                "Represents an accounting ledger account recorded in a general ledger "
                "recording debits and credits."
            ),
        },
        {
            "label": "Customer / Client Account",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/"
                "ClientsAndAccounts/CustomerAccount"
            ),
            "rationale": (
                "Represents a business relationship account held by a client with the "
                "financial institution."
            ),
        },
    ],
    "position": [
        {
            "label": "Financial Position",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/"
                "FinancialProductsAndServices/Position"
            ),
            "rationale": (
                "Represents holding or commitment in a financial asset, currency, or contract."
            ),
        },
        {
            "label": "Equity Position",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/"
                "EquityInstruments/EquityPosition"
            ),
            "rationale": "Represents ownership interest or position in equity instruments.",
        },
    ],
    "facility": [
        {
            "label": "Credit / Loan Facility",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/CreditFacility"
            ),
            "rationale": (
                "Represents a credit arrangement or revolving loan commitment between "
                "lender and borrower."
            ),
        },
        {
            "label": "Physical Facility / Real Estate",
            "fibo_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/Places/Facilities/Facility",
            "rationale": (
                "Represents a physical building, plant, data center, or tangible property."
            ),
        },
    ],
    "party": [
        {
            "label": "Autonomous Legal Entity / Legal Person",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/"
                "LegalPersons/LegalEntity"
            ),
            "rationale": (
                "Represents an individual person or corporate entity with legal rights "
                "and obligations."
            ),
        },
        {
            "label": "Contractual Party / Counterparty",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/ContractParty"
            ),
            "rationale": (
                "Represents a party bound by or participating in a legal contract or agreement."
            ),
        },
    ],
}

__all__ = ["KNOWN_POLYSEMIC_DEFINITIONS"]
