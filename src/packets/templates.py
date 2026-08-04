"""Per-payer appeal letter templates -- the boilerplate/formatting shell
around the LLM-drafted body, distinct from pricing rules (which is why
this doesn't live on `domain.contract.ContractVersion`; see
`src/db/models.py`'s `Contract.packet_template` docstring)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PacketTemplate:
    salutation: str
    letterhead: str
    closing: str
    footer_legal_text: str


DEFAULT_TEMPLATE = PacketTemplate(
    salutation="To Whom It May Concern:",
    letterhead="Appeal of Claim Payment",
    closing="Sincerely,",
    footer_legal_text=(
        "This appeal is submitted pursuant to the terms of the applicable "
        "provider agreement and within the contractual timely-filing window."
    ),
)


def select_template(payer_override: PacketTemplate | None) -> PacketTemplate:
    return payer_override if payer_override is not None else DEFAULT_TEMPLATE
