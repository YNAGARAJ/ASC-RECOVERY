"""X12 837 (professional) claim file parser.

Phase 9 (`docs/MASTER-BUILD-PROMPT-V2.md`): "the 835 alone lacks
diagnosis codes, units and rendering provider, all of which the appeal
packet needs." This module is deliberately narrow -- it exists to
extract exactly those fields (diagnosis codes and rendering provider;
units already comes off the 835 itself, see `domain.x835.ServiceLine835
.units`) and correlate them back to an already-ingested claim, never to
duplicate `domain.x835`'s pricing/reconciliation responsibilities.

**837 is enrichment, not a parallel pricing pipeline.** A claim's dollar
findings are driven entirely by its 835; this parser produces no
`Finding`, no pricing input, nothing `domain.contract`/`domain.variance`
ever sees. Correlation to an existing `claims` row happens by
`patient_control_number` (`CLM01`) -- the *submitter's* claim identifier,
present on both the 837 that submits a claim and the 835 that pays it
(`CLP01`). This is deliberately not `payer_claim_control_number`
(`CLP07` on the 835) -- the payer only assigns that identifier after
adjudication, so it never appears on the claim-submission-side 837 at
all.

Reuses the same ISA-header/segment/element-splitting approach
`domain.x835.parse_835` uses (no third-party X12 library exists in this
project) but duplicated, not imported -- same "second, independent
producer" reasoning `evals/generator.py`'s own low-level segment
builders already establish for not sharing parsing code across
independent producers/consumers of X12 text. `Entity`/`ParseIssue` are
reused directly from `domain.x835` since they're plain, transaction-
set-agnostic data shapes, not parsing control flow.

Only the segments this narrow scope needs are handled: `CLM` (claim,
for `patient_control_number`), `HI` (diagnosis code pointers), `NM1`
(entity loops, filtered to entity identifier code `82` -- rendering
provider, the exact same code `domain.x835.parse_835` filters on).
Everything else in a real 837 (subscriber/payer hierarchical levels,
service-line-level detail, procedure/charge data) is out of scope here
-- this module's only job is diagnosis codes + rendering provider,
correlated by patient control number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.x835 import (
    ISA_ELEMENT_SEP_INDEX,
    ISA_MIN_LENGTH,
    ISA_SUB_ELEMENT_SEP_INDEX,
    ISA_TERMINATOR_INDEX,
    Entity,
    ParseIssue,
)


@dataclass(frozen=True, slots=True)
class Claim837:
    patient_control_number: str
    diagnosis_codes: tuple[str, ...]
    rendering_provider: Entity | None


@dataclass(frozen=True, slots=True)
class Transaction837:
    control_number: str
    claims: tuple[Claim837, ...]


@dataclass(frozen=True, slots=True)
class ParseResult837:
    transactions: tuple[Transaction837, ...]
    errors: tuple[ParseIssue, ...]


# --- Internal mutable builders (not part of the public API) -----------------


@dataclass
class _ClaimBuilder837:
    patient_control_number: str
    diagnosis_codes: list[str] = field(default_factory=list)
    rendering_provider: Entity | None = None


@dataclass
class _TransactionBuilder837:
    control_number: str = ""
    claims: list[Claim837] = field(default_factory=list)


def _safe_element(elements: list[str], index: int, default: str = "") -> str:
    return elements[index] if len(elements) > index and elements[index] else default


def _parse_hi_codes(elements: list[str], sub_element_sep: str) -> list[str]:
    """HI01 through HI12(+): each a composite like `ABK:E119` (qualifier,
    then the actual diagnosis code) -- extract just the code, skipping
    any composite too short to have one."""
    codes: list[str] = []
    for raw in elements[1:]:
        if not raw:
            continue
        parts = raw.split(sub_element_sep)
        if len(parts) >= 2 and parts[1]:
            codes.append(parts[1])
    return codes


def _finalize_claim(claim: _ClaimBuilder837) -> Claim837:
    return Claim837(
        patient_control_number=claim.patient_control_number,
        diagnosis_codes=tuple(claim.diagnosis_codes),
        rendering_provider=claim.rendering_provider,
    )


def _finalize_transaction(txn: _TransactionBuilder837) -> Transaction837:
    return Transaction837(control_number=txn.control_number, claims=tuple(txn.claims))


def parse_837(text: str) -> ParseResult837:
    if not isinstance(text, str):
        raise TypeError(f"parse_837 requires a str, got {type(text).__name__}")

    if len(text) < ISA_MIN_LENGTH or not text.startswith("ISA"):
        return ParseResult837(
            transactions=(),
            errors=(
                ParseIssue(
                    "ISA",
                    0,
                    "ISA segment missing or too short to determine delimiters",
                    "error",
                ),
            ),
        )

    element_sep = text[ISA_ELEMENT_SEP_INDEX]
    sub_element_sep = text[ISA_SUB_ELEMENT_SEP_INDEX]
    terminator = text[ISA_TERMINATOR_INDEX]

    body = text[ISA_MIN_LENGTH:]
    raw_segments = [chunk.strip("\r\n") for chunk in body.split(terminator)]
    raw_segments = [chunk for chunk in raw_segments if chunk]

    errors: list[ParseIssue] = []
    transactions: list[Transaction837] = []

    txn: _TransactionBuilder837 | None = None
    claim_ctx: _ClaimBuilder837 | None = None

    for index, raw in enumerate(raw_segments):
        elements = raw.split(element_sep)
        tag = elements[0] if elements else ""

        try:
            if tag == "ST":
                txn = _TransactionBuilder837(control_number=_safe_element(elements, 2))
            elif tag == "CLM":
                if txn is None:
                    continue
                if claim_ctx is not None:
                    txn.claims.append(_finalize_claim(claim_ctx))
                    claim_ctx = None
                if len(elements) < 2 or not elements[1]:
                    errors.append(
                        ParseIssue("CLM", index, "CLM: missing patient control number", "error")
                    )
                    continue
                claim_ctx = _ClaimBuilder837(patient_control_number=elements[1])
            elif tag == "HI":
                if claim_ctx is None:
                    continue
                claim_ctx.diagnosis_codes.extend(_parse_hi_codes(elements, sub_element_sep))
            elif tag == "NM1":
                if claim_ctx is None:
                    continue
                entity = Entity(
                    entity_identifier_code=elements[1],
                    name=_safe_element(elements, 3),
                    id_qualifier=_safe_element(elements, 8) or None,
                    id_code=_safe_element(elements, 9) or None,
                )
                if entity.entity_identifier_code == "82":
                    claim_ctx.rendering_provider = entity
            elif tag == "SE":
                if txn is None:
                    continue
                if claim_ctx is not None:
                    txn.claims.append(_finalize_claim(claim_ctx))
                    claim_ctx = None
                transactions.append(_finalize_transaction(txn))
                txn = None
            else:
                pass
        except (ValueError, IndexError) as exc:
            errors.append(
                ParseIssue(tag, index, f"{tag}: failed to parse ({type(exc).__name__})", "error")
            )
            if tag == "CLM":
                claim_ctx = None
            continue

    if txn is not None:
        if claim_ctx is not None:
            txn.claims.append(_finalize_claim(claim_ctx))
            claim_ctx = None
        transactions.append(_finalize_transaction(txn))
        errors.append(
            ParseIssue("SE", len(raw_segments), "transaction ended before SE trailer", "warning")
        )

    return ParseResult837(tuple(transactions), tuple(errors))
