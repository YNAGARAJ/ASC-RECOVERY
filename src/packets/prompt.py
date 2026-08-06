"""Builds the prompt sent to the LLM, structurally excluding PHI.

Patient name, member id, payer claim control number, and date of service
never enter `BuiltPrompt.text` as real values -- like the dollar figures
(expected/actual allowed, shortfall), they live only in the
`placeholders` map, used for post-generation substitution
(`render_final_text`). Only procedure code, root cause, and evidence
text are ever real values in the prompt text itself.

F-13 (docs/audit/REGISTER.md): an earlier version of this module
embedded the claim control number and date of service as literal text
and called them "not PHI" in this docstring -- wrong. A claim/account
number is a HIPAA Safe Harbor identifier (#16 account numbers / #18 any
other unique identifying number); a date directly tied to an
individual's care, including date of service, is explicitly identifier
#3. Both are placeholder-only now, same as patient name/member id
always were -- and the real `AnthropicPacketDrafter` (main.py) must not
be enabled against a live tenant before a BAA exists with the LLM
vendor, a compliance precondition this module's data minimization
doesn't substitute for (see docs/compliance/README.md's own checklist;
that gate is process, not something code can enforce).

F-15 (docs/audit/REGISTER.md): pure set-membership validation of the
three dollar tokens after substitution can't tell "the model put the
right figure in the right place" from "the model put a real figure in
the wrong place" (e.g. `ACTUAL_ALLOWED_TOKEN` where
`EXPECTED_ALLOWED_TOKEN` belongs) -- both substitute to a value that's a
member of the allowed set. `required_figure_lines()` fixes the
label-to-token mapping deterministically: the model must reproduce three
exact `"Label: {{TOKEN}}"` strings verbatim, checked by
`packets.service.generate_packet_draft` before substitution. A swapped
token fails that exact-string check even though the swapped value would
still pass plain set membership.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from packets.templates import PacketTemplate

# bandit flags these as B105 "possible hardcoded password" purely on the
# `_TOKEN` suffix in the variable name -- these are template placeholder
# markers substituted by render_final_text(), not secrets or credentials.
PATIENT_TOKEN = "{{PATIENT_REF}}"  # nosec B105
MEMBER_ID_TOKEN = "{{MEMBER_ID}}"  # nosec B105
CLAIM_REFERENCE_TOKEN = "{{CLAIM_REFERENCE}}"  # nosec B105
DATE_OF_SERVICE_TOKEN = "{{DATE_OF_SERVICE}}"  # nosec B105
EXPECTED_ALLOWED_TOKEN = "{{EXPECTED_ALLOWED}}"  # nosec B105
ACTUAL_ALLOWED_TOKEN = "{{ACTUAL_ALLOWED}}"  # nosec B105
SHORTFALL_TOKEN = "{{SHORTFALL}}"  # nosec B105

# F-15 (docs/audit/REGISTER.md): the exact label each dollar token must
# appear immediately after -- see required_figure_lines().
EXPECTED_ALLOWED_LABEL = "Expected allowed amount"
ACTUAL_ALLOWED_LABEL = "Actual amount paid"
SHORTFALL_LABEL = "Shortfall"


def required_figure_lines() -> tuple[str, str, str]:
    """The three exact `"Label: {{TOKEN}}"` substrings a draft must
    contain verbatim -- one per dollar figure, in a label-to-token
    pairing the model cannot swap without failing
    `packets.service.generate_packet_draft`'s validation. Single source
    of truth for both the prompt instructions (`build_prompt`) and the
    post-generation check, so the two can never drift apart."""
    return (
        f"{EXPECTED_ALLOWED_LABEL}: {EXPECTED_ALLOWED_TOKEN}",
        f"{ACTUAL_ALLOWED_LABEL}: {ACTUAL_ALLOWED_TOKEN}",
        f"{SHORTFALL_LABEL}: {SHORTFALL_TOKEN}",
    )


@dataclass(frozen=True, slots=True)
class PromptInput:
    payer_claim_control_number: str
    procedure_code: str
    date_of_service: date
    expected_allowed: str
    actual_allowed: str
    shortfall: str
    root_cause: str
    evidence: str
    patient_name: str | None
    patient_member_id: str | None


@dataclass(frozen=True, slots=True)
class BuiltPrompt:
    text: str
    placeholders: dict[str, str]


def build_prompt(data: PromptInput, template: PacketTemplate) -> BuiltPrompt:
    placeholders = {
        PATIENT_TOKEN: data.patient_name or "the patient",
        MEMBER_ID_TOKEN: data.patient_member_id or "",
        CLAIM_REFERENCE_TOKEN: data.payer_claim_control_number,
        DATE_OF_SERVICE_TOKEN: data.date_of_service.isoformat(),
        EXPECTED_ALLOWED_TOKEN: data.expected_allowed,
        ACTUAL_ALLOWED_TOKEN: data.actual_allowed,
        SHORTFALL_TOKEN: data.shortfall,
    }
    text = (
        f"Draft the body of a professional insurance appeal letter, in the "
        f"style of a letter opening with {template.salutation!r} and closing "
        f"with {template.closing!r}.\n\n"
        f"Claim reference: {CLAIM_REFERENCE_TOKEN}\n"
        f"Procedure code: {data.procedure_code}\n"
        f"Date of service: {DATE_OF_SERVICE_TOKEN}\n"
        f"Reason for underpayment: {data.root_cause}\n"
        f"Supporting evidence: {data.evidence}\n\n"
        "IMPORTANT -- do not write any dollar amount as digits, and do not "
        "compute, adjust, or restate any figure yourself. Include these "
        "three exact lines verbatim, each on its own line, in this exact "
        "form and this exact label-to-value pairing -- never swap which "
        "token follows which label:\n"
        + "\n".join(required_figure_lines())
        + "\n\n"
        f"Also use {CLAIM_REFERENCE_TOKEN} and {DATE_OF_SERVICE_TOKEN} "
        "exactly as shown above wherever the claim reference or date of "
        "service belongs -- never invent or restate either yourself. "
        f"Refer to the patient only as {PATIENT_TOKEN} and, if a member "
        f"id is needed, only as {MEMBER_ID_TOKEN} -- never write or "
        "invent a patient name or member id yourself."
    )
    return BuiltPrompt(text=text, placeholders=placeholders)


def render_final_text(draft: str, placeholders: dict[str, str]) -> str:
    result = draft
    for token, value in placeholders.items():
        result = result.replace(token, value)
    return result
